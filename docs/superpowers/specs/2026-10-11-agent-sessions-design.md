# Agent sessions, scenarios and screenshots: design

Status: approved in discussion 2026-10-11. This spec is waiting for review.

## Context

Scrimmage's goal is to let an FRC student put a robot together from teammates' models and test robot
code before the robot is built, and to let agents run and check those simulations too. The work is in
four sub-projects:

1. Lockstep stepping (done: `docs/superpowers/specs/2026-10-11-lockstep-design.md`)
2. **An agent-facing layer to run and verify simulations, with screenshots** (this document)
3. A part format and an assembler API
4. An interactive GUI. It now also includes a GUI editor for scenario files.

Today an agent can only run a headless check by starting two processes itself and parsing CSV and
log output. This sub-project gives it two ways in:

- **Agents work interactively: "step, look, poke".** They start a simulation, advance it, look at
  it, change something, and repeat. The main interface for this is an **MCP server**.
- **Teams keep lasting checks as scenario files** that anyone can run with `scrimmage run`, an
  agent or CI included. Scenario files don't depend on the robot's language, so Java teams can read
  and write them.
- Students get a UI later (sub-project 4). The CLI still matters for scripted and CI runs.

The MCP server, the CLI and Python users all sit on one library class, `Session`.

## Goals

- An agent can:
  - start a simulation of a robot project
  - hold it paused for as long as it likes
  - advance it by chosen amounts
  - drive it: Driver Station mode, joysticks
  - read its state: ground truth, joints, motors, battery, HAL values, console, NetworkTables
  - poke the world: teleport, push, move objects
  - take screenshots
  - restart the robot program after editing code
- A scenario file describes a run with checks and screenshots. `scrimmage run` executes it and
  writes a report. Exit codes make it usable as a CI gate.
- Every failure report contains the measured value, not just "FAIL".
- A session never hangs silently: a crashed, hung or missing robot program produces an actionable
  error.
- The CLI is reorganized into subcommands (`serve`, `run`). Backward compatibility with the old
  `scrimmage CONFIG ...` form isn't required.

## Non-goals (this sub-project)

- fault injection (dead sensors, stuck motors, brownout)
- spawning new objects (needs sub-project 3's part format)
- plots of logged signals
- a pytest plugin. The Python `Session` API is usable and documented, not packaged as a plugin.
- the GUI and the scenario editor (sub-project 4)
- video
- feeding rendered camera images into robot vision code
- more than one session per MCP server
- lockstep on Windows/macOS

## Architecture

An in-process library. A `Session` owns everything for one run:
- the `World`
- the HALSim `Bridge`
- the robot program's process
- a recorder
- a renderer

The MCP server, the scenario runner and Python users each create Sessions in their own process.
`Session`'s public methods are the boundary: a later sub-project that wants several clients to share
one live simulation can expose them over a socket without redesigning the rest.

The alternatives considered were a simulation daemon with a control API, which was rejected for now
as protocol and supervision work before it's needed, and the MCP server as the only engine, which
was rejected because it ties CI runs to MCP.

### Modules

| Module | Responsibility |
|---|---|
| `scrimmage/session.py` | `Session`: start/stop, `advance(seconds)`, driver inputs, state queries, world pokes, screenshots. The one API everything else uses. |
| `scrimmage/robot_process.py` | Launch, restart or attach to the robot program. Captures console lines with the simulated time they arrived. Runs the program in its own process group and kills it on stop or exit. |
| `scrimmage/driverstation.py` | DS state (mode, joysticks, match time) as data that the bridge sends each DS period. The existing `DSScript` fills it on a timeline. |
| `scrimmage/nt_reader.py` | Lists and reads the robot's NetworkTables topics through `pyntcore` (the optional `nt` extra). |
| `scrimmage/recorder.py` | Per-tick history: ground-truth pose, odometry (from the configured NT topic), motor command/current, battery, contacts. Feeds path overlays and checks. |
| `scrimmage/render.py` | Headless rendering (MuJoCo `Renderer`, EGL or OSMesa). Views: `follow`, `top`, `camera:<name>`, `free`, and contact sheets. Paths are drawn as line geometry added to the scene, so they show in every view. |
| `scrimmage/scenario.py` | Scenario TOML loading and validation, the runner (on a Session), check evaluation, and report writing. |
| `scrimmage/mcp_server.py` | MCP server over stdio using the official `mcp` Python SDK. Each tool is a thin call into `Session`. Installed as the console script `scrimmage-mcp`. |

### Changes to existing code

- **Bridge: a third driver, "held lockstep".** It steps only when `Session.advance()` asks.
  `LockstepDriver`'s step cycle is factored into one `step_once()` shared by the free-running and
  held drivers.
- **Keep-alive.** While held, the bridge writes `>keepalive` (a long counter) every 0.5 s of wall
  time. The extension's watchdog counts any `keepalive` change as activity, so a held robot stays
  paused indefinitely.
- **DS state.** The bridge sends DS frames from a `DriverStationState` object, which the Session
  sets: directly for MCP and Python users, and step by step for scenarios. `DSScript`,
  `--ds-script` and `--exit-after-script` are removed, because scenario files replace them.
- **Lockstep gaps fixed here**, because held sessions make them more likely:
  - In the extension, `release()` setting `engage=false` on itself must not clear an engage request
    that raced in.
  - `LockstepDriver` re-sends `>engage` every 2 s while waiting for `<engaged`. That covers both the
    30 s start-cap release and a lost request.
  - `[lockstep]` in the TOML that isn't a table gives an argparse error, not a traceback.
- **CLI.** Two subcommands replace the old single form (no backward compatibility):
  - `scrimmage serve CONFIG [--launch] [--viewer] [--speed X] [--step S] [--log run.csv]` is
    interactive human use.
    - It waits for a robot program, or with `--launch` starts the one in `[robot_program]`.
    - It runs until interrupted. Lockstep runs free at `--speed` when the extension is present.
    - A human drives it from the WPILib sim GUI's Driver Station as today.
  - `scrimmage run SCENARIO [--out DIR] [--speed X]` runs a scenario file. `speed` overrides the
    scenario's own value.
  - `scrimmage serve` and `run` both report a bad config or scenario as an argparse-style error
    (exit 2).

## Configuration additions (robot config TOML)

```toml
[robot_program]
command = ["python", "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]
cwd = "../examples/robotpy_kitbot"      # relative to this file
env = {}                                # extra environment variables, optional
start_timeout = 30                      # seconds to wait for the robot program to connect

[telemetry]
odometry = "/SmartDashboard/Field/Robot"   # NT topic (Field2d double[3]: x, y, heading deg); optional
```

The bridge picks a free port and passes it to the robot program as `HALSIMWS_PORT`, with
`HALSIMWS_HOST=localhost`. In attach mode it listens on the configured port, by default 3300 as today.

## Session lifecycle

- **`start(attach=False, viewer=False, speed=...)`:**
  - loads the world and starts the bridge
  - launches the robot program, unless attaching
  - waits for the connection and, if the extension is present, for lockstep to engage
  - returns a summary: the devices the robot program created, whether lockstep is active, and the
    first console lines

  The session is then **held at t = 0, disabled**.
- **Without lockstep** (the extension isn't loaded), the session runs in real time. `advance(s)`
  waits `s` seconds of wall time, and the status says time can't be held. Scenarios fail with exit
  code 2 unless they set `require_lockstep = false`.
- **`restart_robot()`:** stops the robot program, resets the world and DS state, relaunches, and
  re-engages. In attach mode it resets the world and tells the caller to restart the robot program
  themselves.
- **`stop()`:** kills the robot program's process group and closes the bridge.

## MCP tools

All results are compact JSON text that includes `t` (the simulated time). Images are PNG image
content.

| Group | Tool | Notes |
|---|---|---|
| Session | `start_session(config, attach=false, viewer=false, replace=false)` | One active session per server. `replace` stops the current one first. |
| | `stop_session()`, `restart_robot()`, `session_status()` | |
| Time | `advance(seconds)` | At most 60 s per call. Returns a state summary plus new console lines. |
| Driver | `set_mode(mode)` | `disabled` \| `auto` \| `teleop` \| `test` |
| | `set_joystick(port, axes, buttons, povs)` | |
| Read | `get_state()` | pose, speed, battery, joints, motors |
| | `get_hal(device_filter)` | raw HAL values |
| | `get_console(since)` | console lines with simulated timestamps |
| | `nt_list(prefix)`, `nt_get(topics)` | |
| Poke | `set_pose(x, y, heading_deg)`, `push(force_n, direction_deg, duration_s)`, `move_body(name, x, y, z, yaw_deg)` | They affect physics only. The robot's odometry isn't reset, which is often the point of the test. |
| Look | `screenshot(view, width, height, paths, camera)` | Views: `follow`, `top`, `camera:<name>`, `free`. Paths are on by default. |
| | `contact_sheet(seconds, frames, view)` | Advances time by `seconds` and returns one grid image. |
| Verify | `run_scenario(path)` | Restarts the robot, runs the scenario in this session, and returns the report plus its screenshots. |

### NetworkTables timing

A robot program flushes NT on a wall-clock timer (about 100 ms). At `--speed max`, NT values lag
the simulation.
- **Interactive use is unaffected,** because wall time passes between agent calls.
- **For checks that read `nt:` signals,** the scenario runner holds the simulation at the check's
  time until NT has had 0.25 s of wall time to flush, then evaluates.
- **`always` checks on `nt:` signals** are evaluated at each 0.25 s hold point rather than every
  tick. The report says so.

## Scenario files

```toml
config = "../configs/kitbot.toml"        # robot config, relative to this file
description = "Two-step auto ends at the scoring position"
speed = "max"                              # default "max"; or a real-time factor
require_lockstep = true                    # default true

[[step]]
mode = "disabled"
duration = 1.0

[[step]]
name = "auto"
mode = "auto"
duration = 9.0

  [[step.check]]
  pose = { x = 4.0, y = 5.035, heading = 90, tol_xy = 0.25, tol_heading = 4 }

  [[step.check]]
  always = { signal = "battery", min = 7.0 }

  [[step.check]]
  never_touch = "obstacle"

  [[step.screenshot]]
  view = "top"
  at = "end"

[[step]]
mode = "teleop"
duration = 3.0
axes = { 0 = [0.0, -0.6, 0.0, 0.0, 0.3, 0.0] }
buttons = { 0 = [false, true] }
```

### Checks

| Check | Meaning |
|---|---|
| `pose = {x, y, heading, tol_xy, tol_heading}` | Ground-truth pose at the end of the step. Any of x/y/heading may be omitted. |
| `odometry_error = {max, when = "end"\|"always"}` | Distance between odometry (the `[telemetry] odometry` topic) and ground truth. |
| `at_end = {signal, min?, max?, equals?, approx?, tol?}` | A signal's value at the end of the step. |
| `always = {signal, min?, max?, equals?}` | Holds on every tick of the step. Reports the first failing time. |
| `never_touch = "<geom or body>"` / `must_touch = "..."` | Contact between the robot and the named object during the step. |
| `console = {contains? , not_contains?}` | Regex over console lines that arrived during the step. |

**Signals:**
- `battery`, `speed`
- `motor.<name>.current`, `motor.<name>.command`
- `joint.<name>.position`, `joint.<name>.velocity`
- `hal:<type>/<device>/<key>`
- `nt:<topic>`

**Implicit checks, always on:**
- the robot program is still running
- no Python `Traceback` and no Java `Exception in thread` / `at <frame>` stack trace in the console
- lockstep active, unless `require_lockstep = false`

**Screenshots:** `[[step.screenshot]] view = ..., at = "end" | <seconds into step>`. A step can
also have `contact_sheet = { frames = N, view = ... }` covering the whole step.

**Validation:** unknown keys, unknown signal names, unknown geom/body/motor/joint names and bad
types are rejected before the run starts, with the valid alternatives listed. That is exit code 2.

### Reports

Reports are written to `runs/<scenario-stem>-<YYYYmmdd-HHMMSS>/`, or to `--out`:

- `report.json`: overall pass/fail. For each step and check: pass/fail, the measured value, the
  expected value, and for `always` checks the simulated time of the first failure. It also lists
  the screenshot paths, the console tail (last 50 lines), and the paths of the log files below.
- `report.md`: a short readable summary with the screenshots inline.
- `run.csv` (today's log format) and `robot.log` (the full console with simulated timestamps).

Exit codes for `scrimmage run`:
- **0**: all checks passed
- **1**: a check failed
- **2**: it couldn't run: invalid scenario, robot never connected, lockstep required but missing,
  missing dependency

A failure message names the measured value, e.g. `pose: 0.41 m from target (x=3.71, y=4.80,
heading=87.2°)`.

## Rendering

- `render.py` creates one `mujoco.Renderer` per session, lazily on the first screenshot.
  `MUJOCO_GL` defaults to `egl` when unset and no display is needed, and the user can override it.
- `follow`: a tracking camera on the robot body. Adjustable distance, azimuth and elevation, with
  sensible defaults.
- `top`: an orthographic-style overhead view framing the whole field (the bounds of the world's
  static geometry).
- `camera:<name>`: a camera defined in the MJCF.
- `free`: a caller-given position and look-at point.
- Paths: ground truth (solid) and odometry (a second color, when available). They're drawn as
  segments added to the `MjvScene` after `update_scene`, decimated to at most 500 segments.
- Contact sheet: N frames captured at equal steps over the span, tiled into a grid (at most 4
  columns) with each frame labeled with its simulated time. Uses Pillow, a new dependency.

## Failure handling

| Situation | Behavior |
|---|---|
| Robot program doesn't connect within `start_timeout` | Start fails. The error has the console tail and the command used. |
| Robot program exits mid-session | The session records "exited (code N) at t=…". `advance` returns that error with the console tail. `restart_robot` recovers. |
| Robot program stops acking steps | `advance` gives up after 10 s of wall time without an ack (`[session] step_timeout`). It reports "robot program stopped responding at t=…" with the console tail, and the session stays usable. |
| Rendering unavailable | Screenshot calls return an error naming `MUJOCO_GL=egl`/`osmesa`. Scenario screenshots are marked skipped with the reason, and checks still run. |
| `pyntcore` missing | `nt_*` tools say to install `scrimmage[nt]`. Scenarios with `nt:` signals or `odometry_error` exit 2. |
| Unknown names | The error lists the valid names. |
| MCP server exits | Robot process groups are killed. In attach mode, the extension's watchdog releases the robot clock. |

MCP tool errors are returned as tool error results. The server never crashes on a tool call.

## Dependencies

- `mcp` (the official Python SDK, version 2.x, which is current on PyPI) in a new `mcp` extra.
- `pillow` as a core dependency, for PNG output and contact sheets.
- `pyntcore` stays in the `nt` extra.

## Testing

- **Unit:**
  - scenario validation and its error messages
  - check evaluation against recorded data
  - DS state frames
  - the recorder
  - rendering: non-blank output, a path overlay present (pixel check along a drawn path), the
    contact-sheet layout
- **Session against `FakeRobot`** (no RobotPy needed):
  - held stepping and the exact amount advanced
  - keep-alive frames while held
  - mode and joystick frames
  - pokes changing the physics state
  - hang detection (a FakeRobot that stops acking)
  - a crash (FakeRobot disconnects)
- **Extension:**
  - keep-alive holds the clock for more than 2 s
  - the engage race fix
  - engage re-sent after a release
- **End to end, real RobotPy kitbot:**
  - a session runs auto through `advance`
  - a top screenshot is non-blank and shows the path
  - an NT SmartDashboard value is read
  - a passing scenario exits 0
  - a deliberately failing scenario exits 1, and the report has measured values
  - `restart_robot` after an injected crash (the probe robot gets a crash switch)
- **MCP:** an `mcp` SDK client talks to `scrimmage-mcp` over stdio. It lists tools, starts a
  session, advances, takes a screenshot and gets image content back.
- **CI:** install `libosmesa6` and render with `MUJOCO_GL=osmesa`.
- **Migration:** the existing e2e tests that use `--ds-script` become scenario files run through
  `scrimmage run`, with the same assertions expressed as checks where possible.
