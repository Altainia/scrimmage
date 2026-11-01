# Agent Sessions, Scenarios and Screenshots Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents can drive a Scrimmage simulation interactively ("step, look, poke") through an MCP server. Teams can keep scenario files with checks and screenshots that `scrimmage run` executes as a CI gate. Everything sits on one `Session` class.

**Architecture:**
- **`Session`** (in-process) owns everything for one run:
  - the `World`
  - a `Bridge` in a new *held lockstep* mode: it steps only on request and feeds the robot's watchdog with keep-alives
  - the robot program process
  - a path recorder
  - a headless renderer
- **The scenario runner and the MCP server** are thin layers over `Session`.
- **The CLI** becomes two subcommands, `serve` and `run`. `DSScript` and the old flags go away.

**Tech Stack:**
- Python 3.11+, with the dev venv at `.venv`
- asyncio
- `websockets` 17
- MuJoCo 3.15 `Renderer`, with EGL/OSMesa
- Pillow
- `pyntcore` (RobotPy 2026.2.2)
- the `mcp` 2.3 SDK (`MCPServer`, `Client`)
- the C++ HALSim extension in `lockstep/`

**Spec:** `docs/superpowers/specs/2026-10-11-agent-sessions-design.md`

## Global Constraints

- Linux. WPILib/RobotPy 2026.2.2. Run everything with the project venv: `.venv/bin/python -m pytest ...`, `.venv/bin/pip ...`. There is no `python` on PATH.
- After any C++ change, rebuild with `.venv/bin/pip install ./lockstep`.
- `ScrimmageLockstep` SimDevice values: `>engage` (bool), `>step_us` (long), `>step_seq` (long), `>keepalive` (long, new), `<engaged` (bool), `<done_seq` (long). `>` means into the robot program, `<` means out of it.
- Keep-alive every 0.5 s of wall time while held (`KEEPALIVE_PERIOD = 0.5`). Extension watchdog 2 s. Engage is re-sent every 2 s while unacknowledged (`ENGAGE_RESEND = 2.0`).
- Defaults:
  - lockstep step 5 ms
  - `[robot_program] start_timeout` 30 s
  - `[session] step_timeout` 10 s
  - attach waits up to 300 s
- MCP `advance` is capped at 60 s per call.
- NT settle hold 0.25 s of wall time (`NT_SETTLE = 0.25`).
- Scenario exit codes: 0 means all checks passed, 1 means a check failed, 2 means it couldn't run (invalid scenario or config, robot never connected, lockstep required but missing, missing dependency).
- A config `command` element that is exactly `"{python}"` is replaced by `sys.executable`.
- Every failure message names the measured value.
- One MCP session per server. Robot programs run in their own process group and are killed with `SIGKILL` on stop.
- New dependencies: `pillow` (core), `mcp>=2.3,<3` (the `mcp` extra and `dev`).
- No backward compatibility for the old CLI form: `scrimmage CONFIG`, `--ds-script`, `--exit-after-script`.
- Commit messages end with a `Co-Authored-By:` trailer naming the model that wrote the commit.

## Review Focus

1. **The agent "thinks" for minutes between calls.** The robot must stay paused: keep-alive, not watchdog release. Pinned in Task 1 (`test_keepalive_holds_clock_past_watchdog`) and Task 4 (`test_held_bridge_steps_only_when_asked`).
2. **The robot program hangs or crashes mid-`advance`.** The call must return an actionable error with the console tail within the step timeout, never hang, and `restart_robot` must recover. Pinned in Task 6 (`test_robot_stall_is_reported`, `test_robot_crash_is_reported_and_restart_recovers`) and Task 11 (`test_session_restart_after_probe_crash`).
3. **The robot program never connects** (a wrong command or cwd, or an immediate exit). `start` must fail with the command and the console, not hang. Pinned in Task 6 (`test_start_fails_when_robot_never_connects`, `test_start_fails_when_robot_exits_immediately`).
4. **Typos in scenario files** (unknown keys, signals, motor, joint or geom names). They must be rejected before the run with the valid names listed, exit 2. Pinned in Task 10 (`test_unknown_*`) and Task 11 (`test_cli_run_rejects_invalid_scenario`).
5. **Screenshots on a machine without GL.** There must be a clear error naming `MUJOCO_GL`, and scenario checks must still run, with the screenshots marked skipped. Pinned in Task 9 (`test_render_unavailable_message`) and Task 11 (`test_runner_marks_screenshots_skipped_when_render_unavailable`).

---

## File Structure

```
lockstep/src/scrimmage_lockstep.cpp      MODIFY  keepalive value; engage race fix (Task 1)
scrimmage/__init__.py                MODIFY  default MUJOCO_GL=egl on Linux (Task 9)
scrimmage/driverstation.py           NEW     DriverStationState, Joystick, MODES (Task 2)
scrimmage/config.py                  NEW     load_config, Config, RobotProgram, ConfigError (Task 3)
scrimmage/lockstep.py                MODIFY  step_once, held mode, engage resend (Task 4)
scrimmage/bridge.py                  MODIFY  ds state, held, bound port, mode events, NT link, recorder; DSScript removed (Tasks 2,4,8,11)
scrimmage/robot_process.py           NEW     RobotProcess, ConsoleLine (Task 5)
scrimmage/session.py                 NEW     Session, SessionError (Tasks 6-9)
scrimmage/world.py                   MODIFY  joint_states, pokes, robot_contacts (Tasks 6,7,10)
scrimmage/nt_reader.py               NEW     NetworkTablesLink, NTUnavailable (Task 8)
scrimmage/recorder.py                NEW     Recorder (Task 8)
scrimmage/render.py                  NEW     Renderer, contact_sheet, to_png, RenderUnavailable (Task 9)
scrimmage/checks.py                  NEW     signals and check evaluators (Task 10)
scrimmage/scenario.py                NEW     scenario model, loading, validation (Task 10)
scrimmage/scenario_runner.py         NEW     run_scenario, Report (Task 11)
scrimmage/__main__.py                REWRITE serve/run subcommands (Task 11)
scrimmage/mcp_server.py              NEW     MCP server (Task 12)
configs/kitbot.toml                   MODIFY  [robot_program], [telemetry] (Task 11)
scenarios/kitbot_auto.toml            NEW     example scenario (Task 11)
tests/fake_robot.py                   MODIFY  ignore_engages, stall_after, exit_after, verbose, closed() (Tasks 4,6)
tests/fake_robot_main.py              NEW     FakeRobot as a process (Task 6)
tests/fake_robot.toml                 NEW     config whose robot program is fake_robot_main.py (Task 6)
tests/lockstep_probe/robot.py         MODIFY  PROBE_CRASH_AT_LOOP (Task 11)
tests/probe.toml                      NEW     config for the probe robot (Task 11)
tests/scenarios/*.toml                NEW     e2e scenarios (Task 11)
tests/test_*.py                       NEW/MODIFY per task
pyproject.toml, .github/workflows/ci.yml, README.md, .gitignore   MODIFY (Tasks 9,12,13)
```

`scenario.py` from the spec's module table is split into `scenario.py` (the file model), `checks.py` (signals and checks) and `scenario_runner.py` (execution and reports). Each stays focused, and `checks.py` is unit-testable without a robot.

---

### Task 1: Extension: keep-alive and the engage race fix

**Files:**
- Modify: `lockstep/src/scrimmage_lockstep.cpp`
- Test: `tests/test_lockstep_ext.py` (append)

**Interfaces:**
- Produces: the `keepalive` SimDevice value (long, input). Any change to it counts as activity for the 2 s watchdog while engaged.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_lockstep_ext.py`:

```python
def test_keepalive_holds_clock_past_watchdog():
    async def body(sim):
        await sim.engage()
        for n in range(1, 5):
            await sim.step(n)
        loops = sim.get(PROBE, "<loops")
        for k in range(1, 9):                       # 4 s of keep-alives: twice the 2 s watchdog
            await sim.send(DEV, {">keepalive": k})
            await asyncio.sleep(0.5)
        assert sim.get(DEV, "<engaged") is True
        assert sim.get(PROBE, "<loops") == loops    # still paused
    asyncio.run(with_robot(body))


def test_engage_after_watchdog_release_engages_again():
    async def body(sim):
        await sim.engage()
        await sim.step(1)
        await sim.until(lambda: sim.get(DEV, "<engaged") is False, 4)   # watchdog released (engage set false)
        await sim.engage()                                               # a fresh engage must not be swallowed
        assert sim.get(DEV, "<engaged") is True
    asyncio.run(with_robot(body))
```

- [ ] **Step 2: Confirm the keep-alive test fails.** Run `.venv/bin/python -m pytest tests/test_lockstep_ext.py -k "keepalive or engages_again" -v`.
  - Expected: `test_keepalive_holds_clock_past_watchdog` FAILS (`<engaged` is False, because the watchdog released).
  - `test_engage_after_watchdog_release_engages_again` may already pass. It's a regression guard: the race it covers (an engage arriving between the extension reading and writing `engage`) is microseconds wide and can't be forced from Python.

- [ ] **Step 3: Implement it in `lockstep/src/scrimmage_lockstep.cpp`.**

In `struct State`:
- add `keepalive = 0` to the handle list, so the line reads `HAL_SimValueHandle engage = 0, stepUs = 0, stepSeq = 0, engaged = 0, doneSeq = 0, keepalive = 0;`
- add these fields:

```cpp
  bool keepalivePending = false;  // guarded by m
  bool selfRelease = false;       // guarded by m: the next engage=false is our own release(), not scrimmage
```

Replace `OnEngage` with:

```cpp
void OnEngage(const char*, void* param, HAL_SimValueHandle, int32_t, const HAL_Value* value) {
  auto* s = static_cast<State*>(param);
  {
    std::lock_guard lock{s->m};
    if (!value->data.v_boolean && s->selfRelease) {
      s->selfRelease = false;  // release() writing engage=false; leave any request that raced in alone
    } else {
      s->engagePending = value->data.v_boolean;
      s->disengagePending = !value->data.v_boolean;
    }
  }
  s->cv.notify_one();
}

void OnKeepalive(const char*, void* param, HAL_SimValueHandle, int32_t, const HAL_Value*) {
  auto* s = static_cast<State*>(param);
  {
    std::lock_guard lock{s->m};
    s->keepalivePending = true;
  }
  s->cv.notify_one();
}
```

In `Worker`, replace the `release` lambda with:

```cpp
  auto release = [&](const char* why) {
    HALSIM_ResumeTiming();
    mode = Mode::kFree;
    HAL_SetSimValueBoolean(s->engaged, false);
    if (HAL_GetSimValueBoolean(s->engage)) {  // only a real change fires OnEngage
      {
        std::lock_guard l{s->m};
        s->selfRelease = true;
      }
      HAL_SetSimValueBoolean(s->engage, false);  // so scrimmage's next engage=true is a change
    }
    Log(why);
  };
```

In `Worker`'s loop:
- add `|| s->keepalivePending` to the `wait_for` predicate.
- after `const bool wantDisengage = s->disengagePending;`, add:

```cpp
    const bool keepalive = s->keepalivePending;
    s->keepalivePending = false;
```

- immediately after `auto now = Clock::now();`, add:

```cpp
    if (keepalive && mode == Mode::kEngaged) lastActivity = now;  // scrimmage is holding us paused on purpose
```

In `HALSIM_InitExtension`, after `s->doneSeq = ...`, add:

```cpp
  s->keepalive = HAL_CreateSimValueLong(dev, "keepalive", HAL_SimValueInput, 0);
```

and after the `stepSeq` callback registration, add:

```cpp
  HALSIM_RegisterSimValueChangedCallback(s->keepalive, s, OnKeepalive, false);
```

- [ ] **Step 4: Rebuild and run the tests.** Run `.venv/bin/pip install ./lockstep && .venv/bin/python -m pytest tests/test_lockstep_ext.py -v`. Expected: all pass, the existing extension tests included.

- [ ] **Step 5: Commit.**

```bash
git add lockstep/src/scrimmage_lockstep.cpp tests/test_lockstep_ext.py
git commit -m "Lockstep extension: keep-alive holds the clock; don't swallow an engage that races a release"
```

---

### Task 2: `DriverStationState`

**Files:**
- Create: `scrimmage/driverstation.py`
- Modify: `scrimmage/bridge.py` (`DSScript.frames` removed; `_on_tick` sends from `self.ds`)
- Test: `tests/test_driverstation.py` (new), `tests/test_bridge.py` (append)

**Interfaces:**
- Produces:
  - `MODES = ("disabled", "auto", "teleop", "test")`
  - `Joystick(axes, buttons, povs)`
  - `DriverStationState` with fields `active: bool`, `mode: str`, `match_time: float`, `joysticks: dict[int, Joystick]` and `changed: bool`, and these methods:
    - `set_mode(mode)`, which raises `ValueError` listing the modes
    - `set_joystick(port, axes=None, buttons=None, povs=None)`, where `port` is 0–5 and anything else raises `ValueError`
    - `clear()`
    - `frames() -> list[str]`
  - `Bridge.ds: DriverStationState`. The bridge sends `ds.frames()` when `ds.active`, and when `ds.changed` is set or `DS_PERIOD` has elapsed.

- [ ] **Step 1: Write the failing tests.**

`tests/test_driverstation.py`:

```python
import json

import pytest

from scrimmage.driverstation import DriverStationState


def test_frames_carry_mode_and_joysticks():
    ds = DriverStationState(active=True)
    ds.set_mode("auto")
    ds.set_joystick(0, axes=[0.0, -0.5], buttons=[True, False])
    frames = [json.loads(f) for f in ds.frames()]
    assert frames[0] == {"type": "Joystick", "device": "0",
                         "data": {">axes": [0.0, -0.5], ">buttons": [True, False], ">povs": [-1]}}
    dsf = frames[-1]["data"]
    assert dsf[">enabled"] is True and dsf[">autonomous"] is True and dsf[">test"] is False


def test_disabled_and_clear():
    ds = DriverStationState(active=True)
    ds.set_mode("teleop")
    ds.set_joystick(1, axes=[1.0])
    ds.clear()
    frames = [json.loads(f) for f in ds.frames()]
    assert len(frames) == 1 and frames[0]["data"][">enabled"] is False


def test_bad_mode_and_port_list_the_valid_values():
    ds = DriverStationState()
    with pytest.raises(ValueError, match="disabled, auto, teleop, test"):
        ds.set_mode("autonomous")
    with pytest.raises(ValueError, match="0-5"):
        ds.set_joystick(6, axes=[0.0])


def test_setters_mark_changed():
    ds = DriverStationState()
    ds.changed = False
    ds.set_mode("auto")
    assert ds.changed
```

Append to `tests/test_bridge.py`:

```python
def test_bridge_sends_ds_state_and_mode_changes_immediately():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None)
        bridge.ds.active = True
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: len(robot.step_seqs()) >= 20)
                assert robot.ds_enabled() and not any(robot.ds_enabled())
                n = len(robot.step_seqs())
                bridge.ds.set_mode("teleop")
                await wait_until(lambda: any(robot.ds_enabled()))
                # sent on the bridge's next tick, not up to DS_PERIOD (4 steps) later; a few steps may be in flight
                first_on = next(i for i, m in enumerate(robot.frames)
                                if m["type"] == "DriverStation" and m["data"][">enabled"])
                steps_before = sum(1 for m in robot.frames[:first_on]
                                   if (m["type"], m["device"]) == lockstep_mod.LOCKSTEP_DEVICE
                                   and ">step_seq" in m["data"])
                assert steps_before <= n + 3
    asyncio.run(main())
```

- [ ] **Step 2: Confirm they fail.** Run `.venv/bin/python -m pytest tests/test_driverstation.py tests/test_bridge.py -k "frames or clear or bad_mode or changed or ds_state" -v`. Expected: `ModuleNotFoundError: scrimmage.driverstation`, and `AttributeError: 'Bridge' object has no attribute 'ds'`.

- [ ] **Step 3: Implement it.**

`scrimmage/driverstation.py`:

```python
"""Driver Station state that scrimmage sends to the robot program when it, not a human, is the DS."""

from __future__ import annotations

from dataclasses import dataclass, field

from .protocol import frame

MODES = ("disabled", "auto", "teleop", "test")


@dataclass
class Joystick:
    axes: list[float] = field(default_factory=list)
    buttons: list[bool] = field(default_factory=list)
    povs: list[int] = field(default_factory=lambda: [-1])


@dataclass
class DriverStationState:
    """active=False leaves the DS to someone else (the WPILib sim GUI): the bridge then sends no DS frames.
    changed asks the bridge to send on its next tick instead of waiting for the next DS period."""

    active: bool = False
    mode: str = "disabled"
    match_time: float = -1.0
    joysticks: dict[int, Joystick] = field(default_factory=dict)
    changed: bool = True

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
        self.mode = mode
        self.changed = True

    def set_joystick(self, port: int, axes=None, buttons=None, povs=None) -> None:
        if not 0 <= int(port) <= 5:
            raise ValueError(f"joystick port must be 0-5, not {port}")
        js = self.joysticks.setdefault(int(port), Joystick())
        if axes is not None:
            js.axes = [float(a) for a in axes]
        if buttons is not None:
            js.buttons = [bool(b) for b in buttons]
        if povs is not None:
            js.povs = [int(p) for p in povs]
        self.changed = True

    def clear(self) -> None:
        self.mode = "disabled"
        self.joysticks.clear()
        self.match_time = -1.0
        self.changed = True

    def frames(self) -> list[str]:
        out = [frame("Joystick", str(p), {">axes": j.axes, ">buttons": j.buttons, ">povs": j.povs})
               for p, j in sorted(self.joysticks.items())]
        m = self.mode
        out.append(frame("DriverStation", "", {
            ">enabled": m != "disabled", ">autonomous": m == "auto", ">test": m == "test",
            ">ds": True, ">station": "blue1", ">match_time": self.match_time, ">new_data": True}))
        return out
```

In `scrimmage/bridge.py`:
- add `from .driverstation import DriverStationState`
- delete `DSScript.frames` (keep `__init__` and `at`)
- in `Bridge.__init__`, add `self.ds = DriverStationState()`
- replace the DS block of `_on_tick` (from `mode = "external"` through the `if phase is None and self.exit_after_script:` block) with:

```python
        mode = self.ds.mode if self.ds.active else "external"
        if self.ds_script is not None:
            phase = self.ds_script.at(now)
            self.ds.active = True
            if phase is None:
                if self.ds.mode != "disabled" or self.ds.joysticks:
                    self.ds.clear()
                mode = "done"
                if self.exit_after_script:
                    self.done.set()
            else:
                if phase["mode"] != self.ds.mode:
                    self.ds.set_mode(phase["mode"])
                mode = phase["mode"]
                self.ds.joysticks.clear()
                for idx, axes in phase.get("axes", {}).items():
                    self.ds.joysticks[int(idx)] = Joystick(list(axes), list(phase.get("buttons", {}).get(idx, [])))
            self.ds.match_time = max(0.0, self.ds_script.total - now)
        if self.ds.active and (self.ds.changed or now - t.ds >= DS_PERIOD):
            t.ds = now
            self.ds.changed = False
            await self._send(self.ds.frames())
```

Also import `Joystick`: `from .driverstation import DriverStationState, Joystick`.

- [ ] **Step 4: Run the tests.** Run `.venv/bin/python -m pytest tests/test_driverstation.py tests/test_bridge.py -v`. Expected: all pass, the existing DS-script tests included.

- [ ] **Step 5: Run the full suite, then commit.** Run `.venv/bin/python -m pytest -q`; expect all to pass.

```bash
git add scrimmage/driverstation.py scrimmage/bridge.py tests/test_driverstation.py tests/test_bridge.py
git commit -m "Add DriverStationState; the bridge sends DS frames from it"
```

---

### Task 3: Config loading

**Files:**
- Create: `scrimmage/config.py`
- Modify: `scrimmage/__main__.py` (use `load_config`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `ConfigError(ValueError)`
  - `RobotProgram(command: list[str], cwd: Path, env: dict[str, str], start_timeout: float)`
  - `Config(path: Path, raw: dict, step: float, robot_program: RobotProgram | None, odometry_topic: str | None, nt_port: int, step_timeout: float)`, with the property `dir`
  - `load_config(path) -> Config`
  - `DEFAULT_ODOMETRY = "/SmartDashboard/Field/Robot"`

- [ ] **Step 1: Write the failing tests.** `tests/test_config.py`:

```python
import sys
from pathlib import Path

import pytest

from scrimmage.config import DEFAULT_ODOMETRY, ConfigError, load_config

ROOT = Path(__file__).resolve().parents[1]


def write(tmp_path, text):
    p = tmp_path / "robot.toml"
    p.write_text(text)
    return p


def test_kitbot_defaults():
    c = load_config(ROOT / "configs" / "kitbot.toml")
    assert c.step == 0.005
    assert c.dir == ROOT / "configs"
    assert c.odometry_topic == DEFAULT_ODOMETRY
    assert c.nt_port == 5810 and c.step_timeout == 10.0


def test_robot_program_and_python_placeholder(tmp_path):
    c = load_config(write(tmp_path, '[robot_program]\ncommand = ["{python}", "robot.py"]\ncwd = "sub"\n'
                                    'env = { A = 1 }\nstart_timeout = 5\n'))
    rp = c.robot_program
    assert rp.command == [sys.executable, "robot.py"]
    assert rp.cwd == (tmp_path / "sub").resolve()
    assert rp.env == {"A": "1"} and rp.start_timeout == 5.0


@pytest.mark.parametrize("text, msg", [
    ("lockstep = 5\n", "[lockstep] must be a table"),
    ('[lockstep]\nstep = "fast"\n', "[lockstep] step must be a number"),
    ('[robot_program]\ncommand = "python robot.py"\n', "[robot_program] command must be a list of strings"),
    ("[telemetry]\nodometry = 3\n", "[telemetry] odometry must be"),
    ('[session]\nstep_timeout = "x"\n', "[session] step_timeout must be a number"),
])
def test_bad_values(tmp_path, text, msg):
    with pytest.raises(ConfigError, match=msg.replace("[", r"\[").replace("]", r"\]")):
        load_config(write(tmp_path, text))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")
```

- [ ] **Step 2: Confirm they fail.** Run `.venv/bin/python -m pytest tests/test_config.py -v`. Expected: `ModuleNotFoundError: scrimmage.config`.

- [ ] **Step 3: Implement it.**

`scrimmage/config.py`:

```python
"""Loading a robot config TOML: the world, device bindings, and how to run the robot program."""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .lockstep import DEFAULT_STEP

DEFAULT_ODOMETRY = "/SmartDashboard/Field/Robot"  # WPILib Field2d robot pose: double[3] x, y, heading deg


class ConfigError(ValueError):
    pass


@dataclass
class RobotProgram:
    command: list[str]
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)
    start_timeout: float = 30.0


@dataclass
class Config:
    path: Path
    raw: dict
    step: float = DEFAULT_STEP
    robot_program: RobotProgram | None = None
    odometry_topic: str | None = DEFAULT_ODOMETRY
    nt_port: int = 5810
    step_timeout: float = 10.0

    @property
    def dir(self) -> Path:
        return self.path.parent


def _table(raw: dict, name: str) -> dict:
    v = raw.get(name, {})
    if not isinstance(v, dict):
        raise ConfigError(f"[{name}] must be a table, not {v!r}")
    return v


def _number(table: dict, key: str, default: float, section: str) -> float:
    v = table.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"[{section}] {key} must be a number of seconds, not {v!r}")
    return float(v)


def load_config(path: str | Path) -> Config:
    path = Path(path).resolve()
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None

    step = _number(_table(raw, "lockstep"), "step", DEFAULT_STEP, "lockstep")

    program = None
    rp = _table(raw, "robot_program")
    if rp:
        cmd = rp.get("command")
        if not (isinstance(cmd, list) and cmd and all(isinstance(c, str) for c in cmd)):
            raise ConfigError('[robot_program] command must be a list of strings, '
                              'e.g. ["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client"]')
        env = rp.get("env", {})
        if not isinstance(env, dict):
            raise ConfigError("[robot_program] env must be a table of NAME = value")
        program = RobotProgram([sys.executable if c == "{python}" else c for c in cmd],
                               (path.parent / str(rp.get("cwd", "."))).resolve(),
                               {str(k): str(v) for k, v in env.items()},
                               _number(rp, "start_timeout", 30.0, "robot_program"))

    tel = _table(raw, "telemetry")
    odometry = tel.get("odometry", DEFAULT_ODOMETRY)
    if odometry is not None and not isinstance(odometry, str):
        raise ConfigError(f"[telemetry] odometry must be a NetworkTables topic name, not {odometry!r}")
    nt_port = tel.get("nt_port", 5810)
    if isinstance(nt_port, bool) or not isinstance(nt_port, int):
        raise ConfigError(f"[telemetry] nt_port must be a port number, not {nt_port!r}")

    sess = _table(raw, "session")
    return Config(path, raw, step, program, odometry or None, nt_port,
                  _number(sess, "step_timeout", 10.0, "session"))
```

In `scrimmage/__main__.py`:
- replace `cfg = tomllib.loads(args.config.read_text())` and the whole `step = args.step / if step is None: ...` block with:

```python
    try:
        config = load_config(args.config)
    except ConfigError as e:
        ap.error(str(e))
    cfg = config.raw
    step = args.step if args.step is not None else config.step
```

- change `World(cfg, args.config.parent)` to `World(cfg, config.dir)`
- add `from .config import ConfigError, load_config`
- remove `import tomllib`

- [ ] **Step 4: Run the tests.** Run `.venv/bin/python -m pytest tests/test_config.py tests/test_e2e.py -k "config or cli" -v`. Expected: pass. `test_cli_rejects_bad_speed_and_step` still matches "[lockstep] step must be a number".

- [ ] **Step 5: Commit.**

```bash
git add scrimmage/config.py scrimmage/__main__.py tests/test_config.py
git commit -m "Add config loading: robot_program, telemetry, session sections with clear errors"
```

---

### Task 4: Held lockstep in the bridge

**Files:**
- Modify: `scrimmage/lockstep.py`, `scrimmage/bridge.py`, `tests/fake_robot.py`
- Test: `tests/test_bridge.py` (append)

**Interfaces:**
- Consumes: `Bridge._connected`, `_restart_clock`, `_rewind_log`, `_on_tick`, `_push_sensors`, `_send`, `lockstep_changed`, `physics_per_step` (existing).
- Produces:
  - In `lockstep.py`: `KEEPALIVE_PERIOD = 0.5`, `ENGAGE_RESEND = 2.0`.
  - `LockstepDriver.step_once(timeout: float | None = None) -> str` returns `"ok"`, `"done"`, `"disconnected"` or `"timeout"`.
  - `LockstepDriver.seq: int`.
  - `LockstepDriver._wait_for(pred, what, timeout=None) -> str` returns `"ok"`, `"disconnected"` or `"timeout"`.
  - `Bridge(..., held: bool = False)`.
  - New `Bridge` attributes:
    - `listening: asyncio.Event`
    - `bound_port: int | None`
    - `mode: str | None` (`"lockstep"` or `"realtime"`, reset to `None` on connect and disconnect)
    - `mode_changed: asyncio.Event`
    - `driver: LockstepDriver | None` (set while lockstep is engaged)
  - New `Bridge` methods: `_set_mode(mode)`, `_lockstep_engaged(driver)`, `_lockstep_ended(driver)`.
  - `FakeRobot(..., ignore_engages: int = 0)`.

- [ ] **Step 1: Write the failing tests.** In `tests/fake_robot.py`:
  - add the `ignore_engages: int = 0` parameter to `FakeRobot.__init__` and store it as `self.ignore_engages`
  - in `_read`, replace `if data.get(">engage") is True:` with:

```python
                if data.get(">engage") is True:
                    if self.ignore_engages > 0:
                        self.ignore_engages -= 1
                    else:
                        await self._send({"<engaged": True})
```

  - add these helpers:

```python
    def keepalives(self) -> list[int]:
        return [m["data"][">keepalive"] for m in self.frames
                if (m["type"], m["device"]) == LOCKSTEP_DEVICE and ">keepalive" in m["data"]]
```

Append to `tests/test_bridge.py`:

```python
def test_held_bridge_steps_only_when_asked():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True) as robot:
                await wait_until(lambda: bridge.mode == "lockstep" and bridge.driver is not None)
                await asyncio.sleep(1.2)
                assert robot.step_seqs() == [] and bridge.world.time == 0.0
                assert len(robot.keepalives()) >= 2          # feeding the watchdog while held
                for _ in range(10):
                    assert await bridge.driver.step_once() == "ok"
                assert robot.step_seqs() == list(range(1, 11))
                assert bridge.world.time == pytest.approx(0.05)
    asyncio.run(main())


def test_step_once_times_out_when_robot_stops_acking():
    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, auto_ack=False):
                await wait_until(lambda: bridge.driver is not None)
                t0 = time.monotonic()
                assert await bridge.driver.step_once(timeout=0.3) == "timeout"
                assert 0.3 <= time.monotonic() - t0 < 1.0
    asyncio.run(main())


def test_engage_is_resent_until_acknowledged(monkeypatch):
    monkeypatch.setattr(lockstep_mod, "ENGAGE_RESEND", 0.2)

    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port, lockstep=True, ignore_engages=2) as robot:
                await wait_until(lambda: bridge.mode == "lockstep")
                assert robot.engage_values().count(True) >= 3
    asyncio.run(main())


def test_port_zero_binds_a_free_port():
    async def main():
        bridge = make_bridge(0, speed=None, held=True)
        async with running(bridge):
            await asyncio.wait_for(bridge.listening.wait(), 2)
            assert bridge.bound_port and bridge.bound_port > 0
            async with FakeRobot(bridge.bound_port, lockstep=True):
                await wait_until(lambda: bridge.mode == "lockstep")
    asyncio.run(main())


def test_held_realtime_fallback_is_not_an_error(monkeypatch):
    monkeypatch.setattr(bridge_mod, "DETECT_WINDOW", 0.2)

    async def main():
        port = next_port()
        bridge = make_bridge(port, speed=None, held=True)
        async with running(bridge):
            async with FakeRobot(port):
                await wait_until(lambda: bridge.mode == "realtime")
                assert not bridge.done.is_set() and bridge.exit_code == 0
    asyncio.run(main())
```

- [ ] **Step 2: Confirm they fail.** Run `.venv/bin/python -m pytest tests/test_bridge.py -k "held or step_once or resent or port_zero" -v`. Expected: `TypeError: ... unexpected keyword argument 'held'`.

- [ ] **Step 3: Implement it in `scrimmage/lockstep.py`.**
  - Add the constants below `STALL_REPEAT`:

```python
KEEPALIVE_PERIOD = 0.5     # wall seconds between keep-alives while held
ENGAGE_RESEND = 2.0        # re-send engage this often until the extension acknowledges it
```

  - Replace `_wait_for`, `run` and `_loop`, and add `_engage`, `step_once` and `_hold`, as follows:

```python
    async def _wait_for(self, pred: Callable[[], bool], what: str, timeout: float | None = None) -> str:
        """Wait until pred() holds. Returns "ok", "disconnected", or "timeout" (after `timeout` wall seconds).
        Warns while the robot program is stalled."""
        b = self.b
        start = time.monotonic()
        next_warn = start + STALL_FIRST_WARNING
        while not pred():
            if not b._connected(self.conn):
                return "disconnected"
            now = time.monotonic()
            if timeout is not None and now - start >= timeout:
                return "timeout"
            wake = next_warn if timeout is None else min(next_warn, start + timeout)
            b.lockstep_changed.clear()
            try:
                await asyncio.wait_for(b.lockstep_changed.wait(), max(0.0, wake - now))
            except TimeoutError:
                if time.monotonic() >= next_warn:
                    log.warning("waiting on robot program (%s)", what)
                    next_warn = time.monotonic() + STALL_REPEAT
        return "ok"

    async def _engage(self) -> bool:
        step_us = round(self.b.step * 1e6)
        attempts = 0
        while True:
            attempts += 1
            if attempts > 1:
                log.warning("waiting on robot program (engage); asking again")
            # step_us goes with engage (not with each step_seq) so the extension never reads a stale value
            await self.b._send([frame(*LOCKSTEP_DEVICE, {">step_us": step_us, ">engage": True})])
            r = await self._wait_for(lambda: self._get("<engaged") is True, "engage", timeout=ENGAGE_RESEND)
            if r != "timeout":
                return r == "ok"

    async def run(self) -> None:
        b, w = self.b, self.b.world
        if not await self._engage():
            return
        log.info("lockstep engaged: %g ms steps, %s", b.step * 1000,
                 "held" if b.held else ("speed max" if b.speed is None else f"speed {b.speed:g}x"))
        b._restart_clock()
        b._rewind_log()  # drop rows logged in real time before the hand-off
        self.seq = int(self._get("<done_seq", 0) or 0)  # the extension keeps counting across reconnects
        b._lockstep_engaged(self)
        wall0, sim0 = time.monotonic(), w.time
        try:
            if b.held:
                await self._hold()
            else:
                pacer = Pacer(b.speed)
                pacer.start(w.time)
                await self._loop(pacer)
        finally:
            b._lockstep_ended(self)
            wall, sim = time.monotonic() - wall0, w.time - sim0
            log.info("lockstep: simulated %.1f s in %.1f s (%.1fx real time)", sim, wall,
                     sim / wall if wall > 0 else 0.0)

    async def step_once(self, timeout: float | None = None) -> str:
        """One lockstep step: physics, per-tick extras, sensors, then the robot program.
        Returns "ok", "done", "disconnected", or "timeout" (no ack within `timeout` wall seconds)."""
        b, w = self.b, self.b.world
        for _ in range(b.physics_per_step):
            w.step(b.hal)
        await b._on_tick(w.time)
        if b.done.is_set():
            return "done"
        await b._push_sensors()
        self.seq += 1
        n = self.seq
        await b._send([frame(*LOCKSTEP_DEVICE, {">step_seq": n})])
        return await self._wait_for(lambda: int(self._get("<done_seq", 0) or 0) >= n, f"step {n}", timeout)

    async def _loop(self, pacer: Pacer) -> None:
        b, w = self.b, self.b.world
        while b._connected(self.conn):
            if await self.step_once() != "ok":
                return
            now = w.time
            if pacer.lag(now) > self.max_lag:
                pacer.start(now)  # don't race to make up for a stall (breakpoint, slow code)
            delay = pacer.delay(now)
            if delay > 0:
                await asyncio.sleep(delay)

    async def _hold(self) -> None:
        """Held: steps happen only through step_once (Session.advance). Meanwhile keep the robot's
        2 s watchdog fed, so an agent can take as long as it likes between calls."""
        n = 0
        while self.b._connected(self.conn):
            n += 1
            await self.b._send([frame(*LOCKSTEP_DEVICE, {">keepalive": n})])
            await asyncio.sleep(KEEPALIVE_PERIOD)
```

  - In `LockstepDriver.__init__`, add `self.seq = 0`.

- [ ] **Step 4: Implement it in `scrimmage/bridge.py`.**
  - Add `held: bool = False` to the `__init__` keyword arguments.
  - Add these attributes:

```python
        self.held = held                 # step only when asked (Session), keeping the robot paused in between
        self.listening = asyncio.Event()
        self.bound_port: int | None = None
        self.mode: str | None = None     # "lockstep" or "realtime" once decided for the current connection
        self.mode_changed = asyncio.Event()
        self.driver: LockstepDriver | None = None
```

  - Add these methods:

```python
    def _set_mode(self, mode: str | None) -> None:
        self.mode = mode
        self.mode_changed.set()

    def _lockstep_engaged(self, driver: LockstepDriver) -> None:
        self.driver = driver
        self._set_mode("lockstep")

    def _lockstep_ended(self, driver: LockstepDriver) -> None:
        if self.driver is driver:
            self.driver = None
```

  - In `_handler`: right after `self.conn_id += 1`, add `self._set_mode(None)`. In its `finally`, after `self.ws = None`, add `self._set_mode(None)`.
  - In `_run_realtime`, replace the body of `if not decided and time.monotonic() - wall0 > DETECT_WINDOW:` with:

```python
                decided = True
                if self.speed != 1.0 and not self.held:
                    log.error("--speed %s needs the scrimmage_lockstep extension, but the robot program didn't "
                              "load it (RobotPy: add --scrimmage-lockstep)", "max" if self.speed is None else self.speed)
                    self.exit_code = 2
                    self.done.set()
                    return "done"
                log.info("lockstep extension not loaded; running in real time")
                self._set_mode("realtime")
```

  - In `run`, change `async with websockets.serve(...):` to `async with websockets.serve(...) as server:`. As the first lines inside it, add:

```python
            self.bound_port = server.sockets[0].getsockname()[1]
            self.listening.set()
```

  - In the listening log line, use `self.bound_port` instead of `self.port`.
  - The `speed`/`step` watchdog check in `__init__` stays as it is. A held bridge is always built with `speed=None`.

- [ ] **Step 5: Run the tests.** Run `.venv/bin/python -m pytest tests/test_bridge.py tests/test_lockstep.py -v` (expect all to pass), then `.venv/bin/python -m pytest -q` (all pass).

- [ ] **Step 6: Commit.**

```bash
git add scrimmage/lockstep.py scrimmage/bridge.py tests/fake_robot.py tests/test_bridge.py
git commit -m "Bridge: held lockstep mode (step on request, keep-alive), engage re-send, bound port and mode events"
```

---

### Task 5: `RobotProcess`

**Files:**
- Create: `scrimmage/robot_process.py`
- Test: `tests/test_robot_process.py`

**Interfaces:**
- Produces:
  - `ConsoleLine(index: int, t: float, text: str)`
  - `RobotProcess(command, cwd, env=None, *, clock=lambda: 0.0, log_path=None, echo=False, keep=5000)`
  - `async start(port: int)`, which raises `RuntimeError` naming the command and cwd if the program can't be launched
  - `async stop()` (kills the process group). On Linux the robot program also gets `PR_SET_PDEATHSIG`, so it dies with Scrimmage even after a `kill -9`.
  - `close()`
  - properties `running: bool` and `returncode: int | None`
  - `next_index: int`
  - `since(index) -> list[ConsoleLine]`
  - `tail(n=50) -> list[str]`

- [ ] **Step 1: Write the failing tests.** `tests/test_robot_process.py`:

```python
import asyncio
import os
import sys
import time

import pytest

from scrimmage.robot_process import RobotProcess


def proc(code: str, **kw) -> RobotProcess:
    return RobotProcess([sys.executable, "-c", code], ".", **kw)


def test_captures_lines_with_clock_and_env(tmp_path):
    async def main():
        t = [1.5]
        log = tmp_path / "robot.log"
        p = proc("import os; print('hello'); print(os.environ['HALSIMWS_PORT'])", clock=lambda: t[0], log_path=log)
        await p.start(4321)
        for _ in range(100):
            if p.returncode is not None and p.next_index >= 2:
                break
            await asyncio.sleep(0.02)
        await p.stop()
        p.close()
        assert [(l.t, l.text) for l in p.since(0)] == [(1.5, "hello"), (1.5, "4321")]
        assert p.returncode == 0 and not p.running
        assert "[    1.500] hello" in log.read_text()
    asyncio.run(main())


def test_stop_kills_the_process_group():
    async def main():
        p = proc("import subprocess; c = subprocess.Popen(['sleep', '60']); print(c.pid, flush=True); c.wait()")
        await p.start(1)
        for _ in range(100):
            if p.next_index:
                break
            await asyncio.sleep(0.02)
        child = int(p.tail(1)[0])
        await p.stop()
        for _ in range(100):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                return
            await asyncio.sleep(0.02)
        pytest.fail("grandchild still alive after stop()")
    asyncio.run(main())


def test_bad_command_names_command_and_cwd():
    async def main():
        p = RobotProcess(["/nonexistent/robot"], "/tmp")
        with pytest.raises(RuntimeError, match=r"/nonexistent/robot.*in /tmp"):
            await p.start(1)
    asyncio.run(main())


def test_robot_dies_with_its_parent(tmp_path):
    """Even kill -9 of scrimmage (e.g. an MCP client killing its server) must not orphan the robot program."""
    import subprocess
    script = tmp_path / "parent.py"
    script.write_text(
        "import asyncio, sys\n"
        "from scrimmage.robot_process import RobotProcess\n"
        "async def main():\n"
        "    p = RobotProcess([sys.executable, '-c', 'import os, time; print(os.getpid(), flush=True); time.sleep(60)'], '.')\n"
        "    await p.start(1)\n"
        "    while not p.next_index:\n"
        "        await asyncio.sleep(0.02)\n"
        "    print(p.tail(1)[0], flush=True)\n"
        "    await asyncio.sleep(60)\n"
        "asyncio.run(main())\n")
    parent = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True)
    child = int(parent.stdout.readline())
    parent.kill()
    parent.wait()
    for _ in range(100):
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail("robot program outlived its parent")


def test_since_and_tail():
    async def main():
        p = proc("for i in range(5): print(i)")
        await p.start(1)
        t0 = time.monotonic()
        while p.next_index < 5 and time.monotonic() - t0 < 5:
            await asyncio.sleep(0.02)
        await p.stop()
        assert [l.text for l in p.since(3)] == ["3", "4"]
        assert p.tail(2) == ["3", "4"]
    asyncio.run(main())
```

- [ ] **Step 2: Confirm they fail.** Run `.venv/bin/python -m pytest tests/test_robot_process.py -v`. Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement it.** `scrimmage/robot_process.py`:

```python
"""Runs the robot program under test: launch it, capture its console, stop it."""

from __future__ import annotations

import asyncio
import collections
import ctypes
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


def _die_with_parent() -> None:
    """Linux: SIGKILL the robot program when scrimmage dies, even from kill -9, so it's never orphaned."""
    try:
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL)  # 1 = PR_SET_PDEATHSIG
    except OSError:
        pass


@dataclass
class ConsoleLine:
    index: int   # counts every line ever printed, so callers can ask for "lines since N"
    t: float     # simulated time when the line arrived
    text: str


class RobotProcess:
    def __init__(self, command: list[str], cwd: str | Path, env: dict[str, str] | None = None, *,
                 clock: Callable[[], float] = lambda: 0.0, log_path: Path | None = None,
                 echo: bool = False, keep: int = 5000):
        self.command = list(command)
        self.cwd = Path(cwd)
        self.env = dict(env or {})
        self.clock = clock
        self.log_path = log_path
        self.echo = echo
        self.lines: collections.deque[ConsoleLine] = collections.deque(maxlen=keep)
        self.next_index = 0
        self.proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._logf = None

    async def start(self, port: int) -> None:
        env = dict(os.environ, **self.env, HALSIMWS_HOST="localhost", HALSIMWS_PORT=str(port),
                   PYTHONUNBUFFERED="1")
        if self.log_path is not None and self._logf is None:
            self._logf = open(self.log_path, "a")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                *self.command, cwd=self.cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,  # its own process group, so stop() takes its children too
                preexec_fn=_die_with_parent if sys.platform.startswith("linux") else None)
        except OSError as e:
            raise RuntimeError(f"couldn't start the robot program ({' '.join(self.command)} in {self.cwd}): "
                               f"{e.strerror or e}") from None
        self._reader = asyncio.create_task(self._read(self.proc))

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                return
            self._add(raw.decode(errors="replace").rstrip("\r\n"))

    def _add(self, text: str) -> None:
        line = ConsoleLine(self.next_index, self.clock(), text)
        self.next_index += 1
        self.lines.append(line)
        if self._logf is not None:
            self._logf.write(f"[{line.t:9.3f}] {text}\n")
            self._logf.flush()
        if self.echo:
            print(text, flush=True)

    @property
    def returncode(self) -> int | None:
        return None if self.proc is None else self.proc.returncode

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def since(self, index: int) -> list[ConsoleLine]:
        return [line for line in self.lines if line.index >= index]

    def tail(self, n: int = 50) -> list[str]:
        return [line.text for line in list(self.lines)[-n:]]

    async def stop(self) -> None:
        proc = self.proc
        if proc is not None and proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # SIGKILL: a robot paused by lockstep ignores SIGINT/SIGTERM
            except ProcessLookupError:
                pass
            await proc.wait()
        if self._reader is not None:
            try:
                await asyncio.wait_for(self._reader, 2)
            except (TimeoutError, asyncio.CancelledError):
                pass
            self._reader = None

    def close(self) -> None:
        if self._logf is not None:
            self._logf.close()
            self._logf = None
```

- [ ] **Step 4: Run the tests.** Run `.venv/bin/python -m pytest tests/test_robot_process.py -v`. Expected: 5 passed.

- [ ] **Step 5: Commit.**

```bash
git add scrimmage/robot_process.py tests/test_robot_process.py
git commit -m "Add RobotProcess: launch, console capture with sim timestamps, process-group stop"
```

---

### Task 6: `Session` core: lifecycle, advance, driver inputs, reads

**Files:**
- Create: `scrimmage/session.py`, `tests/fake_robot_main.py`, `tests/fake_robot.toml`, `tests/test_session.py`
- Modify: `tests/fake_robot.py`, `scrimmage/world.py` (add `joint_states`)

**Interfaces:**
- Consumes:
  - `load_config`/`Config` (Task 3)
  - `Bridge(..., held=True)`, `bridge.listening`, `bound_port`, `mode`, `mode_changed`, `driver.step_once(timeout)`, `ds` (Tasks 2 and 4)
  - `RobotProcess` (Task 5)
  - `Pacer`
- Produces:
  - `SessionError(message, *, t=None, console=None)` with `to_dict()`.
  - `Session(config, *, attach=False, viewer=False, speed=None, nt=True, port=None, log_path=None, robot_log=None)`.
  - Session methods:
    - `async start() -> dict`
    - `async stop()`
    - `async restart_robot() -> dict`
    - `async advance(seconds, on_step=None) -> dict`
    - `set_mode(mode) -> dict`
    - `set_joystick(port, axes=None, buttons=None, povs=None) -> dict`
    - `state() -> dict`
    - `hal(device_filter="") -> dict`
    - `console(since=0) -> dict`
    - `status() -> dict`
  - Session properties: `t: float`, `lockstep: bool`, `world`, `bridge`, `robot`.
  - `World.joint_states() -> dict[str, tuple[float, float]]`.
  - `FakeRobot(..., stall_after=None, exit_after=None, verbose=False)` and `closed()`.

- [ ] **Step 1: Write the test helpers.**

In `tests/fake_robot.py`:
- extend `__init__` with `stall_after: int | None = None, exit_after: int | None = None, verbose: bool = False`, storing all three
- in `_read`, replace the `if ">step_seq" in data:` block with:

```python
                if ">step_seq" in data:
                    seq = data[">step_seq"]
                    if self.exit_after is not None and seq >= self.exit_after:
                        print("Traceback (most recent call last):\n  File \"robot.py\", line 1, in teleopPeriodic\n"
                              "RuntimeError: fake robot crashed on purpose", flush=True)
                        os._exit(3)
                    if self.stall_after is not None and seq > self.stall_after:
                        continue  # stop acking: a hung robot program
                    if self.verbose and seq % 100 == 0:
                        print(f"fake robot step {seq}", flush=True)
                    if self.auto_ack:
                        self.done_seq = seq
                        await self._send({"<done_seq": seq})
                    else:
                        self.held.append(seq)
```

- add `import os`, and this method:

```python
    async def closed(self) -> None:
        """Until the bridge closes the connection."""
        if self._task is not None:
            await self._task
```

`tests/fake_robot_main.py`:

```python
"""A robot-program stand-in for Session tests: connects like halsim_ws_client (optionally playing the lockstep
extension) and prints a little, so console capture has something to show.

    python fake_robot_main.py [--lockstep] [--stall-after N] [--exit-after N]
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_robot import FakeRobot  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lockstep", action="store_true")
    ap.add_argument("--stall-after", type=int)
    ap.add_argument("--exit-after", type=int)
    args = ap.parse_args()
    robot = FakeRobot(int(os.environ["HALSIMWS_PORT"]), lockstep=args.lockstep, stall_after=args.stall_after,
                      exit_after=args.exit_after, verbose=True)
    async with robot:
        print("fake robot connected", flush=True)
        await robot.closed()


asyncio.run(main())
```

`tests/fake_robot.toml`:

```toml
# The kitbot world, with tests/fake_robot_main.py standing in for the robot program.
[model]
mjcf = "../models/kitbot.xml"
timestep = 0.001

[robot]
body = "chassis"
start_pose = [2.0, 4.035, 0.0]

[lockstep]
step = 0.005

[robot_program]
command = ["{python}", "fake_robot_main.py", "--lockstep"]
cwd = "."
start_timeout = 15

[[motor]]
name = "left_drive"
pwm = 0
joint = "left_center"
motor = "CIM"
count = 2
gear_ratio = 10.71

[[motor]]
name = "right_drive"
pwm = 1
joint = "right_center"
motor = "CIM"
count = 2
gear_ratio = 10.71
invert = true
```

- [ ] **Step 2: Write the failing tests.** `tests/test_session.py`:

```python
"""Session against tests/fake_robot_main.py running as a real process (no RobotPy needed)."""

import asyncio
import time
from pathlib import Path

import pytest

import scrimmage.bridge as bridge_mod
from fake_robot import FakeRobot, next_port, wait_until
from scrimmage.session import Session, SessionError

TESTS = Path(__file__).resolve().parent
FAKE = TESTS / "fake_robot.toml"


def fake_config(tmp_path, command: str, extra: str = "") -> Path:
    """tests/fake_robot.toml with another [robot_program] command (absolute paths, so it can live in tmp_path)."""
    text = FAKE.read_text().replace('"../models/', f'"{TESTS.parent / "models"}/')
    text = text.replace('command = ["{python}", "fake_robot_main.py", "--lockstep"]', f"command = {command}")
    text = text.replace('cwd = "."', f'cwd = "{TESTS}"')
    p = tmp_path / "robot.toml"
    p.write_text(text + "\n" + extra)
    return p


def run(coro):
    return asyncio.run(coro)


def test_start_holds_at_zero_and_advance_steps_exactly():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            st = await s.start()
            assert st["lockstep"] is True and st["t"] == 0.0
            await asyncio.sleep(0.5)
            assert s.t == 0.0                                 # held: no time passes on its own
            r = await s.advance(0.1)
            assert r["t"] == pytest.approx(0.1)
            assert r["pose"]["x"] == pytest.approx(2.0, abs=0.01)
            assert set(r["motors"]) == {"left_drive", "right_drive"}
            assert "left_center" in r["joints"]
            assert any("fake robot connected" in line["text"] for line in s.console()["lines"])
        finally:
            await s.stop()
    run(main())


def test_set_mode_rejects_unknown_modes():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            assert s.set_mode("teleop")["mode"] == "teleop"
            with pytest.raises(SessionError, match="disabled, auto, teleop, test"):
                s.set_mode("autonomous")
            s.set_joystick(0, axes=[0.0, -0.5])
            with pytest.raises(SessionError, match="0-5"):
                s.set_joystick(7, axes=[0.0])
        finally:
            await s.stop()
    run(main())


def test_hal_lists_devices():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            assert "SimDevice/ScrimmageLockstep" in s.hal("lockstep")
        finally:
            await s.stop()
    run(main())


def test_robot_stall_is_reported(tmp_path):
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep", "--stall-after", "10"]',
                      "[session]\nstep_timeout = 0.5\n")

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            t0 = time.monotonic()
            with pytest.raises(SessionError, match="stopped responding at t=0.05"):
                await s.advance(1.0)
            assert time.monotonic() - t0 < 3
            assert s.state()["t"] == pytest.approx(0.055)   # usable afterwards
        finally:
            await s.stop()
    run(main())


def test_robot_crash_is_reported_and_restart_recovers(tmp_path):
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep", "--exit-after", "10"]')

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            with pytest.raises(SessionError, match=r"exited \(code 3\)") as e:
                await s.advance(1.0)
            assert any("Traceback" in line for line in e.value.console)
            st = await s.restart_robot()
            assert st["lockstep"] is True and st["t"] == 0.0
            assert (await s.advance(0.02))["t"] == pytest.approx(0.02)
        finally:
            await s.stop()
    run(main())


def test_start_fails_when_robot_never_connects(tmp_path):
    cfg = fake_config(tmp_path, '["{python}", "-c", "import time; print(\'sleeping\', flush=True); time.sleep(60)"]',
                      "")
    cfg.write_text(cfg.read_text().replace("start_timeout = 15", "start_timeout = 1"))

    async def main():
        s = Session(cfg, nt=False)
        with pytest.raises(SessionError, match="didn't connect within 1 s") as e:
            await s.start()
        assert "sleeping" in e.value.console
        assert s.robot is None or not s.robot.running
    run(main())


def test_start_fails_when_robot_exits_immediately(tmp_path):
    cfg = fake_config(tmp_path, '["{python}", "-c", "print(\'bad config\'); raise SystemExit(4)"]')

    async def main():
        s = Session(cfg, nt=False)
        with pytest.raises(SessionError, match=r"exited \(code 4\) before it connected") as e:
            await s.start()
        assert "bad config" in e.value.console
    run(main())


def test_realtime_fallback_session(tmp_path, monkeypatch):
    monkeypatch.setattr(bridge_mod, "DETECT_WINDOW", 0.2)
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py"]')

    async def main():
        s = Session(cfg, nt=False)
        try:
            st = await s.start()
            assert st["lockstep"] is False
            t0, w0 = s.t, time.monotonic()
            r = await s.advance(0.3)
            assert r["t"] >= t0 + 0.3 and time.monotonic() - w0 >= 0.28
        finally:
            await s.stop()
    run(main())


def test_attach_mode_waits_for_a_robot_program():
    async def main():
        port = next_port()
        s = Session(FAKE, attach=True, port=port, nt=False)
        start = asyncio.create_task(s.start())
        await asyncio.sleep(0.3)
        async with FakeRobot(port, lockstep=True):
            st = await asyncio.wait_for(start, 10)
            assert st["lockstep"] is True and st["robot"] == "attached"
            assert (await s.advance(0.01))["t"] == pytest.approx(0.01)
            await s.stop()
    run(main())


def test_session_without_robot_program_section(tmp_path):
    cfg = tmp_path / "bare.toml"
    cfg.write_text(FAKE.read_text().split("[robot_program]")[0].replace('"../models/', f'"{TESTS.parent / "models"}/'))
    with pytest.raises(SessionError, match="no \\[robot_program\\]"):
        Session(cfg)
```

- [ ] **Step 3: Confirm they fail.** Run `.venv/bin/python -m pytest tests/test_session.py -v`. Expected: `ModuleNotFoundError: scrimmage.session`.

- [ ] **Step 4: Add `World.joint_states`** to `scrimmage/world.py`, after `robot_speed`:

```python
    def joint_states(self) -> dict[str, tuple[float, float]]:
        """Position and velocity of every named hinge and slide joint."""
        m, d = self.model, self.data
        out = {}
        for j in range(m.njnt):
            if int(m.jnt_type[j]) not in (int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE)):
                continue
            name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, j)
            if name:
                out[name] = (float(d.qpos[m.jnt_qposadr[j]]), float(d.qvel[m.jnt_dofadr[j]]))
        return out
```

- [ ] **Step 5: Implement `scrimmage/session.py`.**

```python
"""Session: one simulation of one robot program, driven step by step.

The MCP server, the scenario runner and Python users all work through this class. A session holds the
robot program paused between calls (lockstep, with keep-alives), so callers can take as long as they like.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from pathlib import Path
from typing import Callable

from .bridge import Bridge
from .config import Config, load_config
from .lockstep import Pacer
from .robot_process import RobotProcess
from .world import World

log = logging.getLogger("scrimmage")

ATTACH_TIMEOUT = 300.0   # wall seconds to wait for a robot program the user starts themselves
REALTIME_POLL = 0.02     # wall seconds between on_step calls when the robot program runs in real time
CONSOLE_LIMIT = 200      # most console lines returned by one advance


class SessionError(RuntimeError):
    """A problem a caller (often an agent) can act on: the message plus the robot console's tail."""

    def __init__(self, message: str, *, t: float | None = None, console: list[str] | None = None):
        super().__init__(message)
        self.t = t
        self.console = console or []

    def to_dict(self) -> dict:
        return {"error": str(self), "t": self.t, "console_tail": self.console}


class Session:
    def __init__(self, config: Config | str | Path, *, attach: bool = False, viewer: bool = False,
                 speed: float | None = None, nt: bool = True, port: int | None = None,
                 log_path: Path | None = None, robot_log: Path | None = None):
        self.config = config if isinstance(config, Config) else load_config(config)
        if not attach and self.config.robot_program is None:
            raise SessionError(f"{self.config.path} has no [robot_program] section: add one (see configs/kitbot.toml), "
                               "or attach to a robot program you start yourself")
        self.attach, self.viewer, self.speed, self.nt = attach, viewer, speed, nt
        self.port = port if port is not None else (3300 if attach else 0)
        self.log_path, self.robot_log = log_path, robot_log
        self.world: World | None = None
        self.bridge: Bridge | None = None
        self.robot: RobotProcess | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ properties
    @property
    def t(self) -> float:
        return self.world.time if self.world is not None else 0.0

    @property
    def lockstep(self) -> bool:
        return self.bridge is not None and self.bridge.mode == "lockstep"

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> dict:
        cfg = self.config
        try:
            self.world = World(cfg.raw, cfg.dir)
            self.bridge = Bridge(self.world, "localhost", self.port, held=True, viewer=self.viewer, nt=self.nt,
                                 log_path=self.log_path, step=cfg.step, speed=None)
        except ValueError as e:
            raise SessionError(f"{cfg.path}: {e}") from None
        self.bridge.ds.active = True
        self._task = asyncio.create_task(self.bridge.run())
        listening = asyncio.ensure_future(self.bridge.listening.wait())
        done, _ = await asyncio.wait({self._task, listening}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        listening.cancel()
        if self._task in done or not self.bridge.listening.is_set():
            exc = self._task.exception() if self._task.done() else None
            await self.stop()
            raise SessionError(f"couldn't start the HALSim server on port {self.port}: {exc}")
        try:
            if not self.attach:
                await self._launch()
            await self._wait_ready()
        except BaseException:
            await self.stop()  # the SessionError already carries the console tail
            raise
        return {**self.status(), "console": self._lines(0)}

    async def _launch(self) -> None:
        rp = self.config.robot_program
        if self.robot is None:
            self.robot = RobotProcess(rp.command, rp.cwd, rp.env, clock=lambda: self.t, log_path=self.robot_log)
        try:
            await self.robot.start(self.bridge.bound_port)
        except RuntimeError as e:
            raise SessionError(str(e)) from None

    async def _wait_ready(self) -> None:
        b = self.bridge
        timeout = ATTACH_TIMEOUT if self.attach else self.config.robot_program.start_timeout
        deadline = time.monotonic() + timeout
        while b.mode is None:
            if self.robot is not None and not self.robot.running:
                await asyncio.sleep(0.1)  # let the console reader catch the last lines
                raise self._error(f"the robot program exited (code {self.robot.returncode}) before it connected")
            left = deadline - time.monotonic()
            if left <= 0:
                if self.attach:
                    raise self._error(f"no robot program connected to port {b.bound_port} within {timeout:g} s")
                raise self._error(f"the robot program didn't connect within {timeout:g} s "
                                  f"(command: {' '.join(self.config.robot_program.command)} "
                                  f"in {self.config.robot_program.cwd})")
            b.mode_changed.clear()
            if b.mode is not None:
                break
            try:
                await asyncio.wait_for(b.mode_changed.wait(), min(left, 0.2))
            except TimeoutError:
                pass

    async def stop(self) -> None:
        if self.robot is not None:
            await self.robot.stop()
            self.robot.close()
        if self.bridge is not None:
            self.bridge.done.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, 5)
            except BaseException:
                pass
            self._task = None

    async def restart_robot(self) -> dict:
        b = self.bridge
        b.ds.clear()
        if self.attach:
            b._restart_clock()
            return {**self.status(), "note": "attach mode: the world was reset; restart the robot program yourself"}
        await self.robot.stop()
        deadline = time.monotonic() + 5
        while b.ws is not None and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        first = self.robot.next_index
        await self._launch()
        await self._wait_ready()
        return {**self.status(), "console": self._lines(first)}

    # ------------------------------------------------------------------ time
    async def advance(self, seconds: float, on_step: Callable[[float], None] | None = None) -> dict:
        """Run the simulation forward by `seconds` of simulated time, then hold it again.
        on_step(t) is called after every step (every lockstep step, or every 20 ms in real time)."""
        if not (isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds > 0):
            raise SessionError(f"seconds must be a positive number, not {seconds!r}")
        self._check_alive()
        b, w = self.bridge, self.world
        first = self.robot.next_index if self.robot is not None else 0
        if b.mode == "lockstep" and b.driver is not None:
            n = max(1, round(seconds / self.config.step))
            pacer = Pacer(self.speed)
            pacer.start(w.time)
            for _ in range(n):
                r = await b.driver.step_once(timeout=self.config.step_timeout)
                if r == "timeout":
                    raise self._error(f"the robot program stopped responding at t={w.time:.3f} "
                                      f"(no step acknowledgement for {self.config.step_timeout:g} s)")
                if r != "ok":
                    await asyncio.sleep(0.1)
                    self._check_alive()
                    raise self._error(f"the robot program disconnected at t={w.time:.3f}")
                if on_step is not None:
                    on_step(w.time)
                delay = pacer.delay(w.time)
                if delay > 0:
                    await asyncio.sleep(delay)
        else:
            target = w.time + seconds - 1e-9
            deadline = time.monotonic() + seconds * 1.5 + 5
            while w.time < target:
                if b.ws is None:
                    await asyncio.sleep(0.1)
                    self._check_alive()
                    raise self._error(f"the robot program disconnected at t={w.time:.3f}")
                if time.monotonic() > deadline:
                    raise self._error("physics isn't keeping up with real time")
                await asyncio.sleep(REALTIME_POLL)
                if on_step is not None:
                    on_step(w.time)
        return {**self.state(), "console": self._lines(first)}

    # ------------------------------------------------------------------ driver inputs
    def set_mode(self, mode: str) -> dict:
        try:
            self.bridge.ds.set_mode(mode)
        except ValueError as e:
            raise SessionError(str(e)) from None
        return self.state()

    def set_joystick(self, port: int, axes=None, buttons=None, povs=None) -> dict:
        try:
            self.bridge.ds.set_joystick(port, axes, buttons, povs)
        except ValueError as e:
            raise SessionError(str(e)) from None
        return self.state()

    # ------------------------------------------------------------------ reads
    def state(self) -> dict:
        w = self.world
        x, y, yaw = w.robot_pose()
        return {
            "t": round(w.time, 4),
            "mode": self.bridge.ds.mode,
            "lockstep": self.lockstep,
            "pose": {"x": round(x, 4), "y": round(y, 4), "heading_deg": round(math.degrees(yaw), 2)},
            "speed": round(w.robot_speed(), 3),
            "battery": round(w.vbatt, 3),
            "motors": {m.name: {"command": round(m.command, 4), "current": round(m.current, 2)} for m in w.motors},
            "joints": {n: {"position": round(p, 5), "velocity": round(v, 4)} for n, (p, v) in w.joint_states().items()},
        }

    def hal(self, device_filter: str = "") -> dict:
        f = device_filter.lower()
        return {f"{typ}/{dev}": dict(vals) for (typ, dev), vals in sorted(self.bridge.hal.devices.items())
                if f in f"{typ}/{dev}".lower()}

    def console(self, since: int = 0) -> dict:
        nxt = self.robot.next_index if self.robot is not None else 0
        return {"t": round(self.t, 4), "lines": self._lines(since), "next": nxt}

    def status(self) -> dict:
        b = self.bridge
        if self.attach:
            robot = "attached"
        elif self.robot is None:
            robot = None
        else:
            robot = {"running": self.robot.running, "returncode": self.robot.returncode,
                     "command": self.robot.command}
        return {"t": round(self.t, 4), "lockstep": self.lockstep, "connected": b is not None and b.ws is not None,
                "port": b.bound_port if b is not None else None, "robot": robot,
                "devices": sorted(f"{t}/{d}" for t, d in b.hal.devices) if b is not None else []}

    # ------------------------------------------------------------------ helpers
    def _lines(self, first: int) -> list[dict]:
        if self.robot is None:
            return []
        return [{"t": round(line.t, 3), "text": line.text} for line in self.robot.since(first)][-CONSOLE_LIMIT:]

    def _error(self, message: str) -> SessionError:
        return SessionError(message, t=round(self.t, 4), console=self.robot.tail(30) if self.robot is not None else [])

    def _check_alive(self) -> None:
        if self.robot is not None and not self.robot.running:
            raise self._error(f"the robot program exited (code {self.robot.returncode}); "
                              "call restart_robot to launch it again")
        if self.bridge is None or self.bridge.ws is None or self.bridge.mode is None:
            raise self._error("no robot program is connected")
```

The tests expect `e.value.console` to contain the lines printed before the failure. `_error` captures them at raise time.

- [ ] **Step 6: Run the tests.** Run `.venv/bin/python -m pytest tests/test_session.py -v`. Expected: 10 passed.

- [ ] **Step 7: Run the full suite, then commit.** Run `.venv/bin/python -m pytest -q`; expect all to pass.

```bash
git add scrimmage/session.py scrimmage/world.py tests/fake_robot.py tests/fake_robot_main.py tests/fake_robot.toml tests/test_session.py
git commit -m "Add Session: held lockstep lifecycle, advance, driver inputs, state/HAL/console reads"
```

---

### Task 7: World pokes

**Files:**
- Modify: `scrimmage/world.py`, `scrimmage/session.py`
- Test: `tests/test_world_pokes.py` (new), `tests/test_session.py` (append)

**Interfaces:**
- Produces:
  - `World.set_robot_pose(x, y, heading_deg)`
  - `World.set_robot_force(fx, fy)`
  - `World.move_object(name, x, y, z, yaw_deg)`
  - `World.movable_objects() -> list[str]`
  - `World.reset()` now also restores moved static geoms
  - `Session.set_pose(x, y, heading_deg) -> dict`
  - `async Session.push(force_n, direction_deg, duration_s) -> dict`
  - `Session.move_body(name, x, y, z=None, yaw_deg=0.0) -> dict`

- [ ] **Step 1: Write the failing tests.** `tests/test_world_pokes.py`:

```python
import tomllib
from pathlib import Path

import pytest

from scrimmage.protocol import HalState
from scrimmage.world import World

ROOT = Path(__file__).resolve().parents[1]


def kitbot() -> World:
    return World(tomllib.loads((ROOT / "configs" / "kitbot.toml").read_text()), ROOT / "configs")


def test_set_robot_pose():
    w = kitbot()
    w.set_robot_pose(5.0, 3.0, 90.0)
    x, y, yaw = w.robot_pose()
    assert (x, y) == pytest.approx((5.0, 3.0)) and yaw == pytest.approx(1.5708, abs=1e-3)
    assert w.robot_speed() == 0.0


def test_force_pushes_robot_along_its_direction():
    w = kitbot()
    w.set_robot_force(600.0, 0.0)  # +x, along the wheels (sideways, tire friction would hold it)
    hal = HalState()
    for _ in range(300):
        w.step(hal)
    x, y, _ = w.robot_pose()
    assert x > 2.0 + 0.05 and abs(y - 4.035) < 0.05
    w.set_robot_force(0.0, 0.0)


def test_move_static_geom_and_reset_restores_it():
    w = kitbot()
    assert "obstacle" in w.movable_objects()
    w.move_object("obstacle", 3.0, 1.0, 0.25, 45.0)
    gid = w.model.geom("obstacle").id
    assert list(w.model.geom_pos[gid]) == pytest.approx([3.0, 1.0, 0.25])
    w.reset()
    assert list(w.model.geom_pos[gid]) == pytest.approx([8.27, 4.035, 0.25])


def test_move_unknown_object_lists_movable_names():
    w = kitbot()
    with pytest.raises(ValueError, match="obstacle"):
        w.move_object("nope", 0, 0, 0, 0)
    with pytest.raises(ValueError, match="robot"):
        w.move_object("chassis", 0, 0, 0, 0)
```

Append to `tests/test_session.py`:

```python
def test_pokes():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            assert s.set_pose(4.0, 2.0, 180.0)["pose"] == {"x": 4.0, "y": 2.0, "heading_deg": pytest.approx(180.0)}
            r = await s.push(400.0, 0.0, 0.3)
            assert r["pose"]["x"] > 4.02 and r["t"] == pytest.approx(0.3)
            assert s.move_body("obstacle", 6.0, 6.0)["t"] == pytest.approx(0.3)
            with pytest.raises(SessionError, match="obstacle"):
                s.move_body("nope", 0, 0)
            with pytest.raises(SessionError, match="duration_s"):
                await s.push(10.0, 0.0, 0.0)
        finally:
            await s.stop()
    run(main())
```

- [ ] **Step 2: Confirm they fail.** Run `.venv/bin/python -m pytest tests/test_world_pokes.py tests/test_session.py -k "pose or force or move or pokes" -v`. Expected: `AttributeError`.

- [ ] **Step 3: Implement it in `scrimmage/world.py`.**

In `__init__`, after `self.free_qadr = ...`:

```python
        self.free_dadr = int(self.model.jnt_dofadr[jnt]) if self.free_qadr is not None else None
        self._geom_pos0 = self.model.geom_pos.copy()    # move_object edits static geoms; reset() restores them
        self._geom_quat0 = self.model.geom_quat.copy()
```

Note that `self.reset()` runs at the end of `__init__`. Put these lines before it.

In `reset()`, as the first lines:

```python
        self.model.geom_pos[:] = self._geom_pos0
        self.model.geom_quat[:] = self._geom_quat0
```

Add these methods:

```python
    def set_robot_pose(self, x: float, y: float, heading_deg: float) -> None:
        """Teleport the robot (at rest). Only physics moves: robot code's odometry is not told."""
        if self.free_qadr is None:
            raise ValueError("the robot body has no free joint, so it can't be moved")
        q = self.free_qadr
        h = math.radians(heading_deg) / 2
        self.data.qpos[q:q + 2] = [x, y]
        self.data.qpos[q + 3:q + 7] = [math.cos(h), 0, 0, math.sin(h)]
        self.data.qvel[self.free_dadr:self.free_dadr + 6] = 0
        mujoco.mj_forward(self.model, self.data)

    def set_robot_force(self, fx: float, fy: float) -> None:
        """A horizontal force (N, world frame) on the robot body, applied until changed."""
        self.data.xfrc_applied[self.robot_body, :3] = [fx, fy, 0.0]

    def movable_objects(self) -> list[str]:
        """Bodies with a free joint (other than the robot), and named static geoms that aren't planes."""
        m = self.model
        names = []
        for b in range(1, m.nbody):
            j = m.body_jntadr[b]
            if b != self.robot_body and j >= 0 and int(m.jnt_type[j]) == int(mujoco.mjtJoint.mjJNT_FREE):
                n = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b)
                if n:
                    names.append(n)
        for g in range(m.ngeom):
            if m.geom_bodyid[g] == 0 and int(m.geom_type[g]) != int(mujoco.mjtGeom.mjGEOM_PLANE):
                n = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g)
                if n:
                    names.append(n)
        return sorted(names)

    def move_object(self, name: str, x: float, y: float, z: float, yaw_deg: float) -> None:
        m = self.model
        h = math.radians(yaw_deg) / 2
        quat = [math.cos(h), 0, 0, math.sin(h)]
        b = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, name)
        if b == self.robot_body:
            raise ValueError("that's the robot: use set_pose to move it")
        if name in self.movable_objects():
            if b >= 0:
                qa, da = int(m.jnt_qposadr[m.body_jntadr[b]]), int(m.jnt_dofadr[m.body_jntadr[b]])
                self.data.qpos[qa:qa + 7] = [x, y, z, *quat]
                self.data.qvel[da:da + 6] = 0
            else:
                g = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, name)
                m.geom_pos[g] = [x, y, z]
                m.geom_quat[g] = quat
            mujoco.mj_forward(m, self.data)
            return
        raise ValueError(f"no movable object {name!r}; movable objects: {', '.join(self.movable_objects()) or 'none'}")
```

- [ ] **Step 4: Implement it in `scrimmage/session.py`.** Add a section after the driver inputs:

```python
    # ------------------------------------------------------------------ pokes (physics only; robot code isn't told)
    def set_pose(self, x: float, y: float, heading_deg: float) -> dict:
        try:
            self.world.set_robot_pose(float(x), float(y), float(heading_deg))
        except ValueError as e:
            raise SessionError(str(e)) from None
        return self.state()

    async def push(self, force_n: float, direction_deg: float, duration_s: float) -> dict:
        """Push the robot with a horizontal force for duration_s of simulated time (this advances time)."""
        if not (isinstance(duration_s, (int, float)) and math.isfinite(duration_s) and duration_s > 0):
            raise SessionError(f"duration_s must be a positive number, not {duration_s!r}")
        a = math.radians(direction_deg)
        self.world.set_robot_force(force_n * math.cos(a), force_n * math.sin(a))
        try:
            return await self.advance(duration_s)
        finally:
            self.world.set_robot_force(0.0, 0.0)

    def move_body(self, name: str, x: float, y: float, z: float | None = None, yaw_deg: float = 0.0) -> dict:
        w = self.world
        if z is None:
            g = mujoco.mj_name2id(w.model, mujoco.mjtObj.mjOBJ_GEOM, name)
            z = float(w.model.geom_pos[g][2]) if g >= 0 else 0.5
        try:
            w.move_object(name, float(x), float(y), float(z), float(yaw_deg))
        except ValueError as e:
            raise SessionError(str(e)) from None
        return self.state()
```

Add `import mujoco` at the top of `session.py`.

- [ ] **Step 5: Run the tests.** Run `.venv/bin/python -m pytest tests/test_world_pokes.py tests/test_session.py -v`. Expected: all pass.

- [ ] **Step 6: Commit.**

```bash
git add scrimmage/world.py scrimmage/session.py tests/test_world_pokes.py tests/test_session.py
git commit -m "Add pokes: teleport the robot, push it, move objects (reset restores them)"
```

---

### Task 8: NetworkTables link and path recorder

**Files:**
- Create: `scrimmage/nt_reader.py`, `scrimmage/recorder.py`
- Modify: `scrimmage/bridge.py` (use the link; record paths), `scrimmage/session.py` (pass the topic and port; NT reads)
- Test: `tests/test_nt_and_recorder.py` (new), `tests/test_session.py` (append)

**Interfaces:**
- Produces:
  - `NTUnavailable(RuntimeError)`
  - `NetworkTablesLink(odometry_topic, server="localhost", port=5810)` with these methods:
    - `publish_truth(x, y, yaw)`
    - `odometry() -> tuple[float, float, float] | None`
    - `topics(prefix="/") -> list[dict]`
    - `get(topic)`, which raises `KeyError`
    - `close()`
  - `Recorder(max_points=20000)` with `truth: list[tuple[t, x, y]]`, `odometry: list[tuple[t, x, y]]`, `reset()`, `sample(t, x, y, odo)` and `paths() -> dict[str, list[tuple[float, float]]]`.
  - `Bridge(..., odometry_topic=DEFAULT_ODOMETRY, nt_port=5810)` with `bridge.recorder: Recorder` (always present) and `bridge.nt_link: NetworkTablesLink | None`.
  - `Session.nt_list(prefix="/") -> dict`, `Session.nt_get(topics: list[str]) -> dict`, `Session.nt_value(topic) -> Any | None`, which raise `SessionError` when NT is unavailable.

- [ ] **Step 1: Write the failing tests.** `tests/test_nt_and_recorder.py`:

```python
import asyncio
import time

import pytest

from scrimmage.recorder import Recorder

ntcore = pytest.importorskip("ntcore")
from scrimmage.nt_reader import NetworkTablesLink  # noqa: E402

PORT = 5891


def test_recorder_paths_and_decimation():
    r = Recorder(max_points=10)
    for i in range(25):
        r.sample(i * 0.02, float(i), 0.0, (float(i), 1.0, 0.0) if i % 2 else None)
    p = r.paths()
    assert len(p["truth"]) <= 10 and p["truth"][0] == (0.0, 0.0) and p["truth"][-1][0] >= 20
    assert all(y == 1.0 for _, y in p["odometry"])
    r.reset()
    assert r.paths() == {"truth": [], "odometry": []}


def wait_for(pred, timeout=5.0):
    t0 = time.monotonic()
    while not pred():
        assert time.monotonic() - t0 < timeout, "timed out"
        time.sleep(0.05)


def test_link_reads_topics_and_odometry(tmp_path):
    srv = ntcore.NetworkTableInstance.create()
    srv.startServer(str(tmp_path / "nt.json"), "", 0, PORT)
    link = None
    try:
        srv.getDoubleTopic("/SmartDashboard/Gyro deg").publish().set(12.5)
        srv.getDoubleArrayTopic("/SmartDashboard/Field/Robot").publish().set([1.0, 2.0, 30.0])
        link = NetworkTablesLink("/SmartDashboard/Field/Robot", port=PORT)
        wait_for(lambda: link.odometry() is not None)
        assert link.odometry() == (1.0, 2.0, 30.0)
        assert {"name": "/SmartDashboard/Gyro deg", "type": "double"} in link.topics("/SmartDashboard")
        assert link.get("/SmartDashboard/Gyro deg") == 12.5
        with pytest.raises(KeyError, match="no NetworkTables topic"):
            link.get("/nope")
    finally:
        if link is not None:
            link.close()
        srv.stopServer()
        ntcore.NetworkTableInstance.destroy(srv)
```

Append to `tests/test_session.py`:

```python
def test_nt_reads_through_session(tmp_path):
    ntcore = pytest.importorskip("ntcore")
    cfg = fake_config(tmp_path, '["{python}", "fake_robot_main.py", "--lockstep"]', "[telemetry]\nnt_port = 5892\n")
    srv = ntcore.NetworkTableInstance.create()
    srv.startServer(str(tmp_path / "nt.json"), "", 0, 5892)
    srv.getDoubleTopic("/SmartDashboard/Battery V").publish().set(12.1)

    async def main():
        s = Session(cfg)
        try:
            await s.start()
            for _ in range(50):
                if s.nt_value("/SmartDashboard/Battery V") is not None:
                    break
                await asyncio.sleep(0.1)
            assert s.nt_get(["/SmartDashboard/Battery V", "/missing"]) == {
                "t": 0.0, "values": {"/SmartDashboard/Battery V": 12.1}, "missing": ["/missing"]}
            assert any(t["name"] == "/SmartDashboard/Battery V" for t in s.nt_list("/SmartDashboard")["topics"])
        finally:
            await s.stop()
    try:
        run(main())
    finally:
        srv.stopServer()
        ntcore.NetworkTableInstance.destroy(srv)


def test_nt_disabled_session_says_how_to_fix():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            with pytest.raises(SessionError, match="NetworkTables"):
                s.nt_list()
        finally:
            await s.stop()
    run(main())
```

- [ ] **Step 2: Confirm they fail.** Run `.venv/bin/python -m pytest tests/test_nt_and_recorder.py tests/test_session.py -k "recorder or link or nt_" -v`. Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement it.**

`scrimmage/recorder.py`:

```python
"""Where the robot has been (ground truth) and where it thought it was (odometry), for path overlays."""

from __future__ import annotations


class Recorder:
    def __init__(self, max_points: int = 20000):
        self.max_points = max_points
        self.truth: list[tuple[float, float, float]] = []      # (t, x, y)
        self.odometry: list[tuple[float, float, float]] = []

    def reset(self) -> None:
        self.truth.clear()
        self.odometry.clear()

    def sample(self, t: float, x: float, y: float, odo: tuple[float, float, float] | None) -> None:
        self.truth.append((t, x, y))
        if odo is not None:
            self.odometry.append((t, odo[0], odo[1]))
        # keep long runs bounded: halve the resolution instead of dropping the start of the path
        if len(self.truth) > self.max_points:
            self.truth = self.truth[::2]
        if len(self.odometry) > self.max_points:
            self.odometry = self.odometry[::2]

    def paths(self) -> dict[str, list[tuple[float, float]]]:
        return {"truth": [(x, y) for _, x, y in self.truth], "odometry": [(x, y) for _, x, y in self.odometry]}
```

`scrimmage/nt_reader.py`:

```python
"""The robot program's NetworkTables as scrimmage sees them: publish ground truth, read odometry, list and read topics.

The robot program is the NT server (port 5810 locally); scrimmage is a client. NT updates travel on the robot's
wall-clock flush timer (~100 ms), so at --speed max values lag the simulation; scenario checks hold for NT_SETTLE.
"""

from __future__ import annotations

from typing import Any


class NTUnavailable(RuntimeError):
    pass


class NetworkTablesLink:
    def __init__(self, odometry_topic: str | None, server: str = "localhost", port: int = 5810):
        try:
            import ntcore
        except ImportError:
            raise NTUnavailable("NetworkTables needs pyntcore: pip install 'scrimmage[nt]'") from None
        self._ntcore = ntcore
        self.inst = ntcore.NetworkTableInstance.create()
        self.inst.startClient4("scrimmage")
        self.inst.setServer(server, port)
        self._all = ntcore.MultiSubscriber(self.inst, ["/"])  # every topic's values arrive, not just ones we ask for
        self._odo = self.inst.getDoubleArrayTopic(odometry_topic).subscribe([]) if odometry_topic else None
        self._truth = self._Pose2d = None
        try:
            from wpimath.geometry import Pose2d
        except ImportError:
            pass
        else:
            self._Pose2d = Pose2d
            self._truth = self.inst.getStructTopic("/MuJoCo/GroundTruthPose", Pose2d).publish()

    def publish_truth(self, x: float, y: float, yaw: float) -> None:
        if self._truth is not None:
            self._truth.set(self._Pose2d(x, y, yaw))

    def odometry(self) -> tuple[float, float, float] | None:
        if self._odo is None:
            return None
        val = self._odo.get()
        return (float(val[0]), float(val[1]), float(val[2])) if len(val) >= 3 else None

    def topics(self, prefix: str = "/") -> list[dict]:
        return sorted(({"name": t.getName(), "type": t.getTypeString()} for t in self.inst.getTopics(prefix)),
                      key=lambda d: d["name"])

    def get(self, topic: str) -> Any:
        v = self.inst.getEntry(topic).getValue()
        if not v.isValid():
            raise KeyError(f"no NetworkTables topic {topic!r} (or it has no value yet)")
        val = v.value()
        if isinstance(val, (bytes, bytearray)):
            return val.hex()
        return list(val) if isinstance(val, (list, tuple)) else val

    def close(self) -> None:
        self.inst.stopClient()
        self._ntcore.NetworkTableInstance.destroy(self.inst)  # otherwise NT threads keep the process alive
```

In `scrimmage/bridge.py`:
- Import `from .config import DEFAULT_ODOMETRY`, `from .nt_reader import NetworkTablesLink, NTUnavailable` and `from .recorder import Recorder`.
- Add these keyword arguments: `odometry_topic: str | None = DEFAULT_ODOMETRY, nt_port: int = 5810`. Store them, and add `self.recorder = Recorder()` and `self.nt_link: NetworkTablesLink | None = None`.
- Replace `_setup_nt` with:

```python
    def _setup_nt(self) -> NetworkTablesLink | None:
        if not self.want_nt:
            return None
        try:
            return NetworkTablesLink(self.odometry_topic, port=self.nt_port)
        except NTUnavailable as e:
            log.info("%s; skipping NetworkTables", e)
            return None
```

- In `_physics`, replace `self._nt = self._setup_nt()` with `self.nt_link = self._setup_nt()`. In its `finally`, add `if self.nt_link is not None: self.nt_link.close()`.
- Replace `_log_row` with:

```python
    def _log_row(self, now: float, mode: str) -> None:
        w = self.world
        x, y, yaw = w.robot_pose()
        odo = None
        if self.nt_link is not None:
            self.nt_link.publish_truth(x, y, yaw)
            odo = self.nt_link.odometry()
        self.recorder.sample(now, x, y, odo)
        if self._writer:
            vals = list(odo) if odo is not None else [math.nan] * 3
            self._writer.writerow([f"{now:.3f}", mode, f"{x:.4f}", f"{y:.4f}", f"{math.degrees(yaw):.2f}",
                                   f"{w.robot_speed():.3f}", f"{w.vbatt:.2f}",
                                   *[f"{v:.4f}" for v in vals],
                                   *[f"{m.command:.3f}" for m in w.motors],
                                   *[f"{m.current:.1f}" for m in w.motors]])
```

- In `_restart_clock`, add `self.recorder.reset()`.
- Remove the old `self._nt` attribute.

In `scrimmage/session.py`:
- In `start()`, pass `odometry_topic=cfg.odometry_topic, nt_port=cfg.nt_port` to `Bridge`.
- Add these methods:

```python
    # ------------------------------------------------------------------ NetworkTables
    def _nt(self):
        link = self.bridge.nt_link if self.bridge is not None else None
        if link is None:
            raise SessionError("NetworkTables isn't available in this session "
                               "(install scrimmage[nt], and don't start the session with nt=False)")
        return link

    def nt_list(self, prefix: str = "/") -> dict:
        return {"t": round(self.t, 4), "topics": self._nt().topics(prefix)}

    def nt_get(self, topics: list[str]) -> dict:
        link = self._nt()
        values, missing = {}, []
        for name in topics:
            try:
                values[name] = link.get(name)
            except KeyError:
                missing.append(name)
        return {"t": round(self.t, 4), "values": values, "missing": missing}

    def nt_value(self, topic: str):
        try:
            return self._nt().get(topic)
        except KeyError:
            return None
```

- [ ] **Step 4: Run the tests.** Run `.venv/bin/python -m pytest tests/test_nt_and_recorder.py tests/test_session.py tests/test_bridge.py -v` (expect all to pass), then `.venv/bin/python -m pytest -q` (expect all to pass, including `test_lockstep_runs_are_repeatable` with NT on).

- [ ] **Step 5: Commit.**

```bash
git add scrimmage/nt_reader.py scrimmage/recorder.py scrimmage/bridge.py scrimmage/session.py tests/test_nt_and_recorder.py tests/test_session.py
git commit -m "Add NetworkTables link (list/read topics, odometry) and a path recorder"
```

---

### Task 9: Headless rendering and Session screenshots

**Files:**
- Create: `scrimmage/render.py`, `tests/test_render.py`
- Modify: `scrimmage/__init__.py` (default `MUJOCO_GL`), `scrimmage/session.py`, `pyproject.toml` (add `pillow`)
- Test: `tests/test_session.py` (append)

**Interfaces:**
- Produces:
  - `RenderUnavailable(RuntimeError)`
  - `Renderer(world)` with `render(view="follow", *, width=960, height=600, paths=None, camera=None) -> np.ndarray` (RGB uint8) and `close()`
  - `contact_sheet(frames: list[tuple[float, np.ndarray]], columns=4) -> np.ndarray`
  - `to_png(img) -> bytes`
  - `VIEWS_HELP: str`
  - `Session.screenshot(view="follow", *, width=960, height=600, paths=True, camera=None) -> bytes`
  - `async Session.contact_sheet(seconds, frames=8, view="follow", *, width=480, height=300, paths=True, camera=None) -> bytes`
  - `Session.render(view, ...) -> np.ndarray`
  - Views are `follow`, `top`, `free` and `camera:<name>`. `camera` is a dict with any of `lookat`, `distance`, `azimuth` and `elevation`.

- [ ] **Step 1: Add the dependency.** In `pyproject.toml`, change `dependencies` to `["mujoco>=3.1", "websockets>=12", "numpy", "pillow"]`. Run `.venv/bin/pip install -e .` (Pillow is likely already present through matplotlib).

- [ ] **Step 2: Write the failing tests.** `tests/test_render.py`:

```python
import tomllib
from pathlib import Path

import numpy as np
import pytest

from scrimmage.render import Renderer, RenderUnavailable, contact_sheet, to_png
from scrimmage.world import World

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def world():
    return World(tomllib.loads((ROOT / "configs" / "kitbot.toml").read_text()), ROOT / "configs")


def test_follow_and_top_views_are_not_blank(world):
    r = Renderer(world)
    try:
        for view in ("follow", "top"):
            img = r.render(view, width=320, height=200)
            assert img.shape == (200, 320, 3) and img.std() > 5, view
    finally:
        r.close()


def test_paths_are_drawn(world):
    r = Renderer(world)
    try:
        plain = r.render("top", width=480, height=300).astype(int)
        drawn = r.render("top", width=480, height=300,
                         paths={"truth": [(2.0, 4.0), (10.0, 4.0)], "odometry": [(2.0, 2.0), (10.0, 2.0)]}).astype(int)
        changed = np.abs(drawn - plain).sum(axis=2) > 60
        assert changed.sum() > 200
        greenish = drawn[changed]
        assert (greenish[:, 1] > greenish[:, 0]).any() and (greenish[:, 0] > greenish[:, 1]).any()  # both colors
    finally:
        r.close()


def test_large_images_and_free_view(world):
    r = Renderer(world)
    try:
        assert r.render("free", width=1920, height=1200,
                        camera={"lookat": [8, 4, 0], "distance": 10}).shape == (1200, 1920, 3)
        with pytest.raises(ValueError, match="lookat"):
            r.render("free")
    finally:
        r.close()


def test_named_cameras(tmp_path, world):
    r = Renderer(world)
    try:
        with pytest.raises(ValueError, match="the model has no cameras"):
            r.render("camera:front")
        with pytest.raises(ValueError, match='"follow", "top"'):
            r.render("sideways")
    finally:
        r.close()
    xml = (ROOT / "models" / "kitbot.xml").read_text().replace(
        '<freejoint name="chassis_free"/>', '<freejoint name="chassis_free"/>\n<camera name="front" pos="0.4 0 0.5" xyaxes="0 -1 0 0 0 1"/>')
    (tmp_path / "cam.xml").write_text(xml)
    cfg = tomllib.loads((ROOT / "configs" / "kitbot.toml").read_text())
    cfg["model"]["mjcf"] = str(tmp_path / "cam.xml")
    w2 = World(cfg, ROOT / "configs")
    r2 = Renderer(w2)
    try:
        assert r2.render("camera:front", width=160, height=100).std() > 1
    finally:
        r2.close()


def test_contact_sheet_layout_and_png():
    frames = [(i * 0.25, np.full((50, 80, 3), 40 * i, np.uint8)) for i in range(6)]
    sheet = contact_sheet(frames, columns=4)
    assert sheet.shape == (100, 320, 3)
    assert to_png(sheet)[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_unavailable_message(world, monkeypatch):
    import mujoco

    def boom(*a, **k):
        raise RuntimeError("no GL")
    monkeypatch.setattr(mujoco, "Renderer", boom)
    with pytest.raises(RenderUnavailable, match="MUJOCO_GL=egl"):
        Renderer(world).render("follow")
```

Append to `tests/test_session.py`:

```python
def test_screenshot_and_contact_sheet():
    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            png = s.screenshot("top", width=320, height=200)
            assert png[:4] == b"\x89PNG"
            sheet = await s.contact_sheet(0.4, frames=4, view="follow", width=160, height=100)
            assert sheet[:4] == b"\x89PNG" and s.t == pytest.approx(0.4)
            with pytest.raises(SessionError, match="follow"):
                s.screenshot("sideways")
        finally:
            await s.stop()
    run(main())
```

- [ ] **Step 3: Confirm they fail.** Run `.venv/bin/python -m pytest tests/test_render.py tests/test_session.py -k "render or view or path or camera or sheet or screenshot" -v`. Expected: `ModuleNotFoundError: scrimmage.render`.

- [ ] **Step 4: Implement it.**

In `scrimmage/__init__.py`, after the docstring and before the imports:

```python
import os
import sys

# Headless rendering needs a GL backend chosen before mujoco is imported; EGL works without a display on Linux.
if sys.platform.startswith("linux"):
    os.environ.setdefault("MUJOCO_GL", "egl")
```

`scrimmage/render.py`:

```python
"""Headless pictures of the world: a follow camera, a top-down field view, model cameras, free cameras, and
contact sheets. Paths (ground truth, odometry) are drawn into the 3D scene, so they show in every view."""

from __future__ import annotations

import io
import math

import mujoco
import numpy as np
from PIL import Image, ImageDraw

PATH_COLORS = {"truth": (0.15, 0.85, 0.25, 1.0), "odometry": (0.95, 0.35, 0.2, 1.0)}
PATH_WIDTH = 0.03        # capsule radius, m
PATH_Z = 0.02            # just above the carpet
MAX_SEGMENTS = 500       # per path
VIEWS_HELP = 'view must be "follow", "top", "free" or "camera:<name>"'


class RenderUnavailable(RuntimeError):
    pass


class Renderer:
    def __init__(self, world):
        self.world = world
        self._r: mujoco.Renderer | None = None
        self._size: tuple[int, int] | None = None

    def close(self) -> None:
        if self._r is not None:
            self._r.close()
            self._r = None

    def _renderer(self, width: int, height: int) -> mujoco.Renderer:
        if self._r is None or self._size != (width, height):
            self.close()
            vis = self.world.model.vis.global_
            vis.offwidth, vis.offheight = max(vis.offwidth, width), max(vis.offheight, height)
            try:
                self._r = mujoco.Renderer(self.world.model, height=height, width=width)
            except Exception as e:  # noqa: BLE001 - any GL failure means the same thing to the caller
                raise RenderUnavailable(
                    f"headless rendering isn't available ({e}). On Linux set MUJOCO_GL=egl, or MUJOCO_GL=osmesa "
                    "with libosmesa6 installed") from None
            self._size = (width, height)
        return self._r

    def _field_bounds(self) -> tuple[float, float, float, float]:
        """(cx, cy, half_x, half_y) of the static, non-plane geometry: the field and its walls."""
        m, d = self.world.model, self.world.data
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for g in range(m.ngeom):
            if m.geom_bodyid[g] != 0 or int(m.geom_type[g]) == int(mujoco.mjtGeom.mjGEOM_PLANE):
                continue
            rot = d.geom_xmat[g].reshape(3, 3)
            c = d.geom_xpos[g] + rot @ m.geom_aabb[g][:3]
            h = np.abs(rot) @ m.geom_aabb[g][3:]
            lo, hi = np.minimum(lo, c - h), np.maximum(hi, c + h)
        if not np.isfinite(lo).all():
            c, e = m.stat.center, m.stat.extent
            return float(c[0]), float(c[1]), float(e), float(e)
        return float((lo[0] + hi[0]) / 2), float((lo[1] + hi[1]) / 2), float((hi[0] - lo[0]) / 2), float((hi[1] - lo[1]) / 2)

    def _camera(self, view: str, camera: dict | None, aspect: float):
        w, c = self.world, camera or {}
        cam = mujoco.MjvCamera()
        if view == "follow":
            cam.type = mujoco.mjtCamera.mjCAMERA_TRACKING
            cam.trackbodyid = w.robot_body
            cam.distance = float(c.get("distance", 3.0))
            cam.azimuth = float(c.get("azimuth", -135.0))
            cam.elevation = float(c.get("elevation", -30.0))
        elif view == "top":
            cx, cy, hx, hy = self._field_bounds()
            fovy = math.radians(w.model.vis.global_.fovy)
            half = max(hy, hx / aspect)  # fovy is vertical; the image is aspect times wider
            cam.type = mujoco.mjtCamera.mjCAMERA_FREE
            cam.lookat[:] = [cx, cy, 0.0]
            cam.azimuth, cam.elevation = 90.0, -90.0
            cam.distance = 1.08 * half / math.tan(fovy / 2)
        elif view == "free":
            if "lookat" not in c:
                raise ValueError("the free view needs camera = {lookat: [x, y, z]} (and optionally distance, "
                                 "azimuth, elevation)")
            cam.type = mujoco.mjtCamera.mjCAMERA_FREE
            cam.lookat[:] = [float(v) for v in c["lookat"]]
            cam.distance = float(c.get("distance", 3.0))
            cam.azimuth = float(c.get("azimuth", -135.0))
            cam.elevation = float(c.get("elevation", -30.0))
        elif view.startswith("camera:"):
            name = view.split(":", 1)[1]
            if mujoco.mj_name2id(w.model, mujoco.mjtObj.mjOBJ_CAMERA, name) < 0:
                names = [mujoco.mj_id2name(w.model, mujoco.mjtObj.mjOBJ_CAMERA, i) for i in range(w.model.ncam)]
                have = ", ".join(n for n in names if n) or None
                raise ValueError(f"no camera {name!r}; " + (f"the model's cameras: {have}" if have
                                                             else "the model has no cameras"))
            return name
        else:
            raise ValueError(f"{VIEWS_HELP}, not {view!r}")
        return cam

    @staticmethod
    def _draw_paths(scene, paths: dict[str, list[tuple[float, float]]]) -> None:
        for kind, pts in paths.items():
            if len(pts) < 2 or kind not in PATH_COLORS:
                continue
            stride = max(1, math.ceil((len(pts) - 1) / MAX_SEGMENTS))
            keep = pts[::stride]
            if keep[-1] != pts[-1]:
                keep.append(pts[-1])
            rgba = np.array(PATH_COLORS[kind], dtype=np.float32)
            for (x0, y0), (x1, y1) in zip(keep, keep[1:]):
                if math.hypot(x1 - x0, y1 - y0) < 1e-6:
                    continue
                if scene.ngeom >= scene.maxgeom:
                    return
                g = scene.geoms[scene.ngeom]
                mujoco.mjv_initGeom(g, mujoco.mjtGeom.mjGEOM_CAPSULE, np.zeros(3), np.zeros(3), np.eye(3).ravel(), rgba)
                mujoco.mjv_connector(g, mujoco.mjtGeom.mjGEOM_CAPSULE, PATH_WIDTH,
                                     np.array([x0, y0, PATH_Z]), np.array([x1, y1, PATH_Z]))
                scene.ngeom += 1

    def render(self, view: str = "follow", *, width: int = 960, height: int = 600,
               paths: dict[str, list[tuple[float, float]]] | None = None, camera: dict | None = None) -> np.ndarray:
        cam = self._camera(view, camera, width / height)  # validate before creating a GL context
        r = self._renderer(width, height)
        r.update_scene(self.world.data, camera=cam)
        if paths:
            self._draw_paths(r.scene, paths)
        return r.render().copy()


def contact_sheet(frames: list[tuple[float, np.ndarray]], columns: int = 4) -> np.ndarray:
    """Tile frames (t, image) left to right, top to bottom, each labeled with its simulated time."""
    cols = min(columns, len(frames))
    rows = math.ceil(len(frames) / cols)
    h, w = frames[0][1].shape[:2]
    sheet = Image.new("RGB", (cols * w, rows * h))
    draw = ImageDraw.Draw(sheet)
    for i, (t, img) in enumerate(frames):
        x, y = (i % cols) * w, (i // cols) * h
        sheet.paste(Image.fromarray(img), (x, y))
        draw.rectangle([x, y, x + 84, y + 20], fill=(0, 0, 0))
        draw.text((x + 6, y + 4), f"t={t:.2f} s", fill=(255, 255, 255))
    return np.asarray(sheet)


def to_png(img: np.ndarray) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="PNG")
    return buf.getvalue()
```

In `scrimmage/session.py`:
- Import `from .render import Renderer, RenderUnavailable, contact_sheet, to_png`.
- In `__init__`, add `self._renderer: Renderer | None = None`.
- In `stop()`, after stopping the bridge task, add `if self._renderer is not None: self._renderer.close(); self._renderer = None`.
- Add:

```python
    # ------------------------------------------------------------------ pictures
    def render(self, view: str = "follow", *, width: int = 960, height: int = 600, paths: bool = True,
               camera: dict | None = None):
        if self._renderer is None:
            self._renderer = Renderer(self.world)
        p = None
        if paths:
            p = self.bridge.recorder.paths()
            x, y, _ = self.world.robot_pose()
            p["truth"] = p["truth"] + [(x, y)]
        try:
            return self._renderer.render(view, width=int(width), height=int(height), paths=p, camera=camera)
        except (RenderUnavailable, ValueError) as e:
            raise SessionError(str(e)) from None

    def screenshot(self, view: str = "follow", *, width: int = 960, height: int = 600, paths: bool = True,
                   camera: dict | None = None) -> bytes:
        return to_png(self.render(view, width=width, height=height, paths=paths, camera=camera))

    async def contact_sheet(self, seconds: float, frames: int = 8, view: str = "follow", *, width: int = 480,
                            height: int = 300, paths: bool = True, camera: dict | None = None) -> bytes:
        """Advance `seconds`, capturing `frames` pictures at equal steps, and tile them into one image."""
        if not 1 <= int(frames) <= 16:
            raise SessionError(f"frames must be 1-16, not {frames}")
        self.render(view, width=width, height=height, paths=paths, camera=camera)  # fail before advancing
        shots = []
        for _ in range(int(frames)):
            await self.advance(seconds / int(frames))
            shots.append((self.t, self.render(view, width=width, height=height, paths=paths, camera=camera)))
        return to_png(contact_sheet(shots))
```

- [ ] **Step 5: Check the top view by eye.** Run this one-off:

```bash
.venv/bin/python -c "
import tomllib; from pathlib import Path; from scrimmage.world import World; from scrimmage.render import Renderer, to_png
w=World(tomllib.loads(Path('configs/kitbot.toml').read_text()), Path('configs'))
Path('/tmp/claude-top.png').write_bytes(to_png(Renderer(w).render('top', paths={'truth':[(2,4.035),(6,4.035),(6,6)]})))"
```

Then Read `/tmp/claude-top.png`. Expected: the whole field fills the frame, its long axis horizontal, with a green path running right from the robot and then up. If the field is rotated 90°, change the top view's `cam.azimuth` to `0.0` (or `180.0`), and say so in the report.

- [ ] **Step 6: Run the tests.** Run `.venv/bin/python -m pytest tests/test_render.py tests/test_session.py -v`. Expected: all pass.

- [ ] **Step 7: Commit.**

```bash
git add scrimmage/__init__.py scrimmage/render.py scrimmage/session.py pyproject.toml tests/test_render.py tests/test_session.py
git commit -m "Add headless rendering: follow/top/free/model-camera views, path overlays, contact sheets"
```

---

### Task 10: Scenario files: model, validation, signals and checks

**Files:**
- Create: `scrimmage/scenario.py`, `scrimmage/checks.py`, `tests/test_scenario.py`
- Modify: `scrimmage/world.py` (add `robot_contacts`, `contact_names`)

**Interfaces:**
- Produces (in `scenario.py`):
  - `ScenarioError(ValueError)`
  - `Shot(view, at, width, height, camera)`, where `at` is `"end"` or seconds into the step
  - `SheetSpec(frames, view, width, height)`
  - `Step(name, mode, duration, axes, buttons, povs, checks: list[dict], screenshots: list[Shot], contact_sheet: SheetSpec | None)`
  - `Scenario(path, config_path, description, speed, require_lockstep, steps)`; `speed` is `float | None`, and `None` means max
  - `load_scenario(path) -> Scenario`
  - `validate(scenario, world)`, which builds every check and validates views
- Produces (in `checks.py`):
  - `Signal(name, read: Callable[[session], Any], uses_nt: bool)`
  - `resolve_signal(name, world) -> Signal`
  - `CheckResult(check, passed, message, measured=None, expected=None, first_failure_t=None)` with `to_dict()`
  - `Check` with attributes `label`, `uses_nt` and `ticks`, and methods `begin(session)`, `tick(session, t)` and `end(session) -> CheckResult`
  - `build_check(spec: dict, world, where: str) -> Check`
  - `CRASH_PATTERN: re.Pattern`
- Also: `World.robot_contacts() -> set[str]` and `World.contact_names() -> list[str]`.

The checks read the session duck-typed: `session.world`, `session.bridge.hal`, `session.robot`, `session.nt_value(topic)` and `session.bridge.nt_link`. That keeps them unit-testable with a stub.

- [ ] **Step 1: Write the failing tests.** `tests/test_scenario.py`:

```python
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scrimmage.checks import CRASH_PATTERN, build_check, resolve_signal
from scrimmage.protocol import HalState
from scrimmage.scenario import ScenarioError, load_scenario, validate
from scrimmage.world import World

ROOT = Path(__file__).resolve().parents[1]

GOOD = """
config = "{config}"
description = "demo"
speed = "max"

[[step]]
mode = "disabled"
duration = 1.0

[[step]]
name = "auto"
mode = "auto"
duration = 2.0
axes = {{ 0 = [0.0, -0.5] }}
  [[step.check]]
  pose = {{ x = 4.0, y = 5.0, heading = 90, tol_xy = 0.25 }}
  [[step.check]]
  always = {{ signal = "battery", min = 7.0 }}
  [[step.check]]
  never_touch = "obstacle"
  [[step.screenshot]]
  view = "top"
  at = 1.5
  [step.contact_sheet]
  frames = 4
"""


def write(tmp_path, text, **fmt):
    p = tmp_path / "s.toml"
    p.write_text(text.format(config=ROOT / "configs" / "kitbot.toml", **fmt))
    return p


@pytest.fixture
def world():
    return World(tomllib.loads((ROOT / "configs" / "kitbot.toml").read_text()), ROOT / "configs")


def test_load_good_scenario(tmp_path, world):
    s = load_scenario(write(tmp_path, GOOD))
    assert s.speed is None and s.require_lockstep is True and s.config_path == ROOT / "configs" / "kitbot.toml"
    assert [st.name for st in s.steps] == ["step 1", "auto"]
    assert s.steps[1].axes == {0: [0.0, -0.5]} and s.steps[1].screenshots[0].at == 1.5
    assert s.steps[1].contact_sheet.frames == 4
    validate(s, world)


@pytest.mark.parametrize("bad, msg", [
    ('speeed = 1\n', "unknown key 'speeed'"),
    ('[[step]]\nmode = "autonomous"\nduration = 1\n', "mode must be one of"),
    ('[[step]]\nmode = "auto"\nduration = -1\n', "duration must be a positive number"),
    ('[[step]]\nmode = "auto"\nduration = 1\n[[step.check]]\npose = {x = 1}\nalways = {signal = "speed", max = 1}\n',
     "exactly one of"),
    ('[[step]]\nmode = "auto"\nduration = 1\n[[step.screenshot]]\nat = 5\n', "beyond the step's 1 s"),
])
def test_unknown_or_bad_values(tmp_path, bad, msg):
    with pytest.raises(ScenarioError, match=msg):
        load_scenario(write(tmp_path, 'config = "{config}"\n' + bad))


def test_unknown_signal_and_names_list_alternatives(world):
    with pytest.raises(ScenarioError, match="left_drive"):
        build_check({"always": {"signal": "motor.lefty.current", "max": 1}}, world, "step 1")
    with pytest.raises(ScenarioError, match="obstacle"):
        build_check({"never_touch": "obstacel"}, world, "step 1")
    with pytest.raises(ScenarioError, match="battery, speed"):
        resolve_signal("voltage", world)


def stub(world, lines=()):
    robot = SimpleNamespace(next_index=0, since=lambda i: [SimpleNamespace(index=n, t=0.0, text=t)
                                                             for n, t in enumerate(lines) if n >= i])
    return SimpleNamespace(world=world, bridge=SimpleNamespace(hal=HalState(), nt_link=None), robot=robot,
                           nt_value=lambda topic: None)


def test_pose_check_reports_measured_values(world):
    s = stub(world)
    c = build_check({"pose": {"x": 4.0, "y": 4.035, "tol_xy": 0.25}}, world, "auto")
    world.set_robot_pose(3.6, 4.035, 0.0)
    r = c.end(s)
    assert not r.passed and "0.40 m from target" in r.message and "x=3.60" in r.message
    world.set_robot_pose(4.1, 4.0, 0.0)
    assert c.end(s).passed


def test_always_check_reports_first_failure_time(world):
    s = stub(world)
    c = build_check({"always": {"signal": "battery", "min": 12.0}}, world, "auto")
    c.begin(s)
    world.vbatt = 12.5
    c.tick(s, 1.0)
    world.vbatt = 11.0
    c.tick(s, 1.25)
    world.vbatt = 10.0
    c.tick(s, 1.5)
    r = c.end(s)
    assert not r.passed and r.first_failure_t == 1.25 and r.measured == 10.0


def test_contact_checks(world):
    s = stub(world)
    never = build_check({"never_touch": "obstacle"}, world, "auto")
    must = build_check({"must_touch": "obstacle"}, world, "auto")
    for c in (never, must):
        c.begin(s)
    world.set_robot_pose(8.27 - 0.9, 4.035, 0.0)   # bumper against the obstacle
    for c in (never, must):
        c.tick(s, 2.0)
    assert not never.end(s).passed and must.end(s).passed


def test_console_check_and_crash_pattern(world):
    s = stub(world, ["step 0: drive", "step 3: stop"])
    c = build_check({"console": {"contains": r"step 3: stop", "not_contains": "Exception"}}, world, "auto")
    c.begin(s)
    assert c.end(s).passed
    assert CRASH_PATTERN.search("Traceback (most recent call last):")
    assert CRASH_PATTERN.search("Exception in thread \"main\" java.lang.NullPointerException")
    assert CRASH_PATTERN.search("\tat frc.robot.Robot.teleopPeriodic(Robot.java:42)")
    assert not CRASH_PATTERN.search("step 3: stop")


def test_at_end_on_nt_signal_needs_nt(world):
    c = build_check({"at_end": {"signal": "nt:/SmartDashboard/Gyro deg", "approx": 0, "tol": 5}}, world, "x")
    assert c.uses_nt
    r = c.end(stub(world))
    assert not r.passed and "no value" in r.message
```

- [ ] **Step 2: Confirm they fail.** Run `.venv/bin/python -m pytest tests/test_scenario.py -v`. Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Add the contact helpers to `scrimmage/world.py`.**

```python
    def robot_contacts(self) -> set[str]:
        """Names of geoms (and their bodies) outside the robot that the robot is touching right now."""
        m, d = self.model, self.data
        root = m.body_rootid[self.robot_body]
        out: set[str] = set()
        for c in d.contact[:d.ncon]:
            b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
            in1, in2 = m.body_rootid[b1] == root, m.body_rootid[b2] == root
            if in1 == in2:
                continue
            g, b = (c.geom2, b2) if in1 else (c.geom1, b1)
            for name in (mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g),
                         mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b)):
                if name:
                    out.add(name)
        return out

    def contact_names(self) -> list[str]:
        """Geom and body names outside the robot, which contact checks can name."""
        m = self.model
        root = m.body_rootid[self.robot_body]
        names = {mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) for g in range(m.ngeom)
                 if m.body_rootid[m.geom_bodyid[g]] != root}
        names |= {mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, b) for b in range(m.nbody)
                  if m.body_rootid[b] != root}
        return sorted(n for n in names if n)
```

- [ ] **Step 4: Implement `scrimmage/checks.py`.**

```python
"""Signals a scenario can check, and the checks themselves. Checks read the session duck-typed (world,
bridge.hal, robot, nt_value), so they can be unit-tested without a robot program."""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable

# Python tracebacks and Java stack traces in the robot console
CRASH_PATTERN = re.compile(r"^Traceback \(most recent call last\)|^Exception in thread|^\s+at [\w.$<>]+\(.*\)\s*$",
                           re.MULTILINE)
SIGNALS_HELP = ("battery, speed, motor.<name>.current|command, joint.<name>.position|velocity, "
                "hal:<type>/<device>/<key>, nt:<topic>")


class ScenarioError(ValueError):
    pass


@dataclass
class Signal:
    name: str
    read: Callable[[Any], Any]
    uses_nt: bool = False


def resolve_signal(name: str, world) -> Signal:
    if name == "battery":
        return Signal(name, lambda s: s.world.vbatt)
    if name == "speed":
        return Signal(name, lambda s: s.world.robot_speed())
    if name.startswith("motor."):
        parts = name.split(".")
        motors = [m.name for m in world.motors]
        if len(parts) != 3 or parts[2] not in ("current", "command") or parts[1] not in motors:
            raise ScenarioError(f"unknown signal {name!r}: use motor.<name>.current or .command; "
                                f"motors: {', '.join(motors) or 'none'}")
        i, field = motors.index(parts[1]), parts[2]
        return Signal(name, lambda s: getattr(s.world.motors[i], field))
    if name.startswith("joint."):
        parts = name.split(".")
        joints = sorted(world.joint_states())
        if len(parts) != 3 or parts[2] not in ("position", "velocity") or parts[1] not in joints:
            raise ScenarioError(f"unknown signal {name!r}: use joint.<name>.position or .velocity; "
                                f"joints: {', '.join(joints) or 'none'}")
        j, k = parts[1], 0 if parts[2] == "position" else 1
        return Signal(name, lambda s: s.world.joint_states()[j][k])
    if name.startswith("hal:"):
        typ, _, rest = name[4:].partition("/")
        dev, _, key = rest.rpartition("/")
        if not (typ and key):
            raise ScenarioError(f"unknown signal {name!r}: use hal:<type>/<device>/<key>, e.g. hal:PWM/0/<speed")
        return Signal(name, lambda s: s.bridge.hal.get(typ, dev, key))
    if name.startswith("nt:") and len(name) > 3:
        topic = name[3:]
        return Signal(name, lambda s: s.nt_value(topic), uses_nt=True)
    raise ScenarioError(f"unknown signal {name!r}; signals are {SIGNALS_HELP}")


@dataclass
class CheckResult:
    check: str
    passed: bool
    message: str
    measured: Any = None
    expected: Any = None
    first_failure_t: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class Check:
    label = ""
    uses_nt = False
    ticks = False  # wants tick() calls during the step

    def begin(self, session) -> None:
        pass

    def tick(self, session, t: float) -> None:
        pass

    def end(self, session) -> CheckResult:
        raise NotImplementedError


def _num(spec: dict, key: str, where: str) -> float | None:
    v = spec.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ScenarioError(f"{where}: {key} must be a number, not {v!r}")
    return float(v)


def _only(spec: dict, allowed: set[str], where: str) -> None:
    extra = set(spec) - allowed
    if extra:
        raise ScenarioError(f"{where}: unknown key {sorted(extra)[0]!r}; allowed: {', '.join(sorted(allowed))}")


class PoseCheck(Check):
    def __init__(self, spec: dict, where: str):
        _only(spec, {"x", "y", "heading", "tol_xy", "tol_heading"}, where)
        self.x, self.y, self.h = _num(spec, "x", where), _num(spec, "y", where), _num(spec, "heading", where)
        if self.x is None and self.y is None and self.h is None:
            raise ScenarioError(f"{where}: pose needs at least one of x, y, heading")
        self.tol_xy = _num(spec, "tol_xy", where) or 0.1
        self.tol_h = _num(spec, "tol_heading", where) or 5.0
        self.label = "pose"

    def end(self, session) -> CheckResult:
        x, y, yaw = session.world.robot_pose()
        hdg = math.degrees(yaw)
        dx = (x - self.x) if self.x is not None else 0.0
        dy = (y - self.y) if self.y is not None else 0.0
        dist = math.hypot(dx, dy)
        dh = abs((hdg - self.h + 180) % 360 - 180) if self.h is not None else 0.0
        ok = dist <= self.tol_xy and dh <= self.tol_h
        measured = {"x": round(x, 3), "y": round(y, 3), "heading": round(hdg, 1)}
        expected = {k: v for k, v in (("x", self.x), ("y", self.y), ("heading", self.h)) if v is not None}
        where = f"(x={x:.2f}, y={y:.2f}, heading={hdg:.1f}°)"
        msg = (f"pose within {dist:.2f} m and {dh:.1f}° of target {where}" if ok else
               f"pose: {dist:.2f} m from target, heading off by {dh:.1f}° {where}; "
               f"allowed {self.tol_xy:g} m, {self.tol_h:g}°")
        return CheckResult(self.label, ok, msg, measured, expected)


class SignalCheck(Check):
    """at_end or always: a signal compared with min / max / equals / approx+tol."""

    def __init__(self, kind: str, spec: dict, world, where: str):
        _only(spec, {"signal", "min", "max", "equals", "approx", "tol"}, where)
        if "signal" not in spec:
            raise ScenarioError(f"{where}: {kind} needs signal = \"...\"")
        self.kind = kind
        self.signal = resolve_signal(str(spec["signal"]), world)
        self.min, self.max = _num(spec, "min", where), _num(spec, "max", where)
        self.equals = spec.get("equals")
        self.approx, self.tol = _num(spec, "approx", where), _num(spec, "tol", where)
        if self.approx is not None and self.tol is None:
            raise ScenarioError(f"{where}: approx needs tol")
        if kind == "always" and self.approx is not None:
            raise ScenarioError(f"{where}: always checks take min, max or equals")
        if self.min is None and self.max is None and self.equals is None and self.approx is None:
            raise ScenarioError(f"{where}: {kind} needs min, max, equals, or approx with tol")
        self.uses_nt = self.signal.uses_nt
        self.ticks = kind == "always"
        self.label = f"{kind} {self.signal.name} {self._expected()}"
        self.first_fail: float | None = None
        self.worst: Any = None
        self.seen = False

    def _expected(self) -> str:
        parts = []
        if self.min is not None:
            parts.append(f">= {self.min:g}")
        if self.max is not None:
            parts.append(f"<= {self.max:g}")
        if self.equals is not None:
            parts.append(f"== {self.equals!r}")
        if self.approx is not None:
            parts.append(f"≈ {self.approx:g} ± {self.tol:g}")
        return " and ".join(parts)

    def _ok(self, v) -> bool:
        if v is None:
            return False
        if self.equals is not None:
            return v == self.equals
        try:
            v = float(v)
        except (TypeError, ValueError):
            return False
        if self.min is not None and v < self.min:
            return False
        if self.max is not None and v > self.max:
            return False
        if self.approx is not None and abs(v - self.approx) > self.tol:
            return False
        return True

    def begin(self, session) -> None:
        self.first_fail, self.worst, self.seen = None, None, False

    def tick(self, session, t: float) -> None:
        v = self.signal.read(session)
        self.seen = True
        if not self._ok(v):
            if self.first_fail is None:
                self.first_fail = round(t, 4)
            if self.worst is None or (isinstance(v, (int, float)) and isinstance(self.worst, (int, float)) and (
                    (self.min is not None and v < self.worst) or (self.max is not None and v > self.worst))):
                self.worst = v

    def end(self, session) -> CheckResult:
        if self.kind == "always":
            if not self.seen:
                self.tick(session, getattr(session, "t", 0.0))
            ok = self.first_fail is None
            msg = (f"{self.signal.name} stayed {self._expected()}" if ok else
                   f"{self.signal.name} was {self.worst!r} (first out of range at t={self.first_fail}); "
                   f"expected {self._expected()}")
            return CheckResult(self.label, ok, msg, self.worst, self._expected(), self.first_fail)
        v = self.signal.read(session)
        if v is None:
            return CheckResult(self.label, False, f"{self.signal.name} has no value at the end of the step",
                               None, self._expected())
        ok = self._ok(v)
        return CheckResult(self.label, ok, f"{self.signal.name} = {v!r} at the end; expected {self._expected()}",
                           v, self._expected())


class OdometryErrorCheck(Check):
    def __init__(self, spec: dict, where: str):
        _only(spec, {"max", "when"}, where)
        self.max = _num(spec, "max", where)
        if self.max is None:
            raise ScenarioError(f"{where}: odometry_error needs max")
        self.when = spec.get("when", "end")
        if self.when not in ("end", "always"):
            raise ScenarioError(f"{where}: odometry_error when must be \"end\" or \"always\"")
        self.uses_nt = True
        self.ticks = self.when == "always"
        self.label = f"odometry_error {self.when} <= {self.max:g} m"
        self.worst, self.first_fail, self.missing = 0.0, None, False

    def _err(self, session) -> float | None:
        link = session.bridge.nt_link
        odo = link.odometry() if link is not None else None
        if odo is None:
            return None
        x, y, _ = session.world.robot_pose()
        return math.hypot(x - odo[0], y - odo[1])

    def begin(self, session) -> None:
        self.worst, self.first_fail, self.missing = 0.0, None, False

    def tick(self, session, t: float) -> None:
        e = self._err(session)
        if e is None:
            self.missing = True
            return
        self.worst = max(self.worst, e)
        if e > self.max and self.first_fail is None:
            self.first_fail = round(t, 4)

    def end(self, session) -> CheckResult:
        if self.when == "end":
            e = self._err(session)
            if e is None:
                return CheckResult(self.label, False, "no odometry on NetworkTables ([telemetry] odometry)", None, self.max)
            return CheckResult(self.label, e <= self.max, f"odometry {e:.3f} m from ground truth at the end; "
                               f"allowed {self.max:g} m", round(e, 4), self.max)
        if self.missing and self.worst == 0.0:
            return CheckResult(self.label, False, "no odometry on NetworkTables ([telemetry] odometry)", None, self.max)
        ok = self.first_fail is None
        return CheckResult(self.label, ok, f"odometry error peaked at {self.worst:.3f} m; allowed {self.max:g} m",
                           round(self.worst, 4), self.max, self.first_fail)


class ContactCheck(Check):
    def __init__(self, target: Any, must: bool, world, where: str):
        if not isinstance(target, str) or target not in world.contact_names():
            raise ScenarioError(f"{where}: no geom or body {target!r} to touch; names: {', '.join(world.contact_names())}")
        self.target, self.must = target, must
        self.ticks = True
        self.label = f"{'must_touch' if must else 'never_touch'} {target}"
        self.first_t: float | None = None

    def begin(self, session) -> None:
        self.first_t = None

    def tick(self, session, t: float) -> None:
        if self.first_t is None and self.target in session.world.robot_contacts():
            self.first_t = round(t, 4)

    def end(self, session) -> CheckResult:
        touched = self.first_t is not None
        ok = touched if self.must else not touched
        msg = (f"touched {self.target} at t={self.first_t}" if touched else f"never touched {self.target}")
        return CheckResult(self.label, ok, msg, touched, self.must, None if self.must else self.first_t)


class ConsoleCheck(Check):
    def __init__(self, spec: dict, where: str):
        _only(spec, {"contains", "not_contains"}, where)
        try:
            self.contains = re.compile(spec["contains"]) if "contains" in spec else None
            self.not_contains = re.compile(spec["not_contains"]) if "not_contains" in spec else None
        except re.error as e:
            raise ScenarioError(f"{where}: bad regex: {e}") from None
        if self.contains is None and self.not_contains is None:
            raise ScenarioError(f"{where}: console needs contains or not_contains")
        self.label = "console " + " ".join(f"{k} /{v}/" for k, v in spec.items())
        self.first = 0

    def begin(self, session) -> None:
        self.first = session.robot.next_index if session.robot is not None else 0

    def end(self, session) -> CheckResult:
        lines = [l.text for l in session.robot.since(self.first)] if session.robot is not None else []
        if self.contains is not None and not any(self.contains.search(l) for l in lines):
            return CheckResult(self.label, False, f"no console line matched /{self.contains.pattern}/ "
                               f"({len(lines)} lines in the step)", None, self.contains.pattern)
        if self.not_contains is not None:
            bad = next((l for l in lines if self.not_contains.search(l)), None)
            if bad is not None:
                return CheckResult(self.label, False, f"console line matched /{self.not_contains.pattern}/: {bad}",
                                   bad, None)
        return CheckResult(self.label, True, "console as expected")


CHECK_KINDS = ("pose", "odometry_error", "at_end", "always", "never_touch", "must_touch", "console")


def build_check(spec: dict, world, where: str) -> Check:
    keys = [k for k in spec if k in CHECK_KINDS]
    if len(keys) != 1 or len(spec) != 1:
        raise ScenarioError(f"{where}: each [[step.check]] needs exactly one of {', '.join(CHECK_KINDS)}; "
                            f"got {', '.join(spec) or 'nothing'}")
    kind, val = keys[0], spec[keys[0]]
    w = f"{where}, {kind}"
    if kind in ("never_touch", "must_touch"):
        return ContactCheck(val, kind == "must_touch", world, w)
    if not isinstance(val, dict):
        raise ScenarioError(f"{w}: expected a table like {kind} = {{ ... }}")
    if kind == "pose":
        return PoseCheck(val, w)
    if kind == "odometry_error":
        return OdometryErrorCheck(val, w)
    if kind == "console":
        return ConsoleCheck(val, w)
    return SignalCheck(kind, val, world, w)
```

- [ ] **Step 5: Implement `scrimmage/scenario.py`.**

```python
"""Scenario files: a sequence of steps (DS mode, duration, joysticks) with checks and screenshots."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .checks import ScenarioError, build_check
from .driverstation import MODES
from .lockstep import parse_speed
from .render import VIEWS_HELP

TOP_KEYS = {"config", "description", "speed", "require_lockstep", "step"}
STEP_KEYS = {"name", "mode", "duration", "axes", "buttons", "povs", "check", "screenshot", "contact_sheet"}
SHOT_KEYS = {"view", "at", "width", "height", "camera"}
SHEET_KEYS = {"frames", "view", "width", "height"}

__all__ = ["ScenarioError", "Shot", "SheetSpec", "Step", "Scenario", "load_scenario", "validate"]


@dataclass
class Shot:
    view: str = "follow"
    at: float | str = "end"
    width: int = 960
    height: int = 600
    camera: dict | None = None


@dataclass
class SheetSpec:
    frames: int = 8
    view: str = "follow"
    width: int = 480
    height: int = 300


@dataclass
class Step:
    name: str
    mode: str
    duration: float
    axes: dict[int, list[float]] = field(default_factory=dict)
    buttons: dict[int, list[bool]] = field(default_factory=dict)
    povs: dict[int, list[int]] = field(default_factory=dict)
    checks: list[dict] = field(default_factory=list)
    screenshots: list[Shot] = field(default_factory=list)
    contact_sheet: SheetSpec | None = None


@dataclass
class Scenario:
    path: Path
    config_path: Path
    description: str
    speed: float | None
    require_lockstep: bool
    steps: list[Step]


def _keys(d: dict, allowed: set[str], where: str) -> None:
    for k in d:
        if k not in allowed:
            raise ScenarioError(f"{where}: unknown key {k!r}; allowed: {', '.join(sorted(allowed))}")


def _ports(v, where: str, what: str) -> dict[int, list]:
    if not isinstance(v, dict):
        raise ScenarioError(f"{where}: {what} must be a table like {what} = {{ 0 = [...] }}")
    out = {}
    for k, vals in v.items():
        if not str(k).isdigit() or not 0 <= int(k) <= 5 or not isinstance(vals, list):
            raise ScenarioError(f"{where}: {what} keys are joystick ports 0-5 with a list of values")
        out[int(k)] = vals
    return out


def _int(d: dict, key: str, default: int, where: str) -> int:
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        raise ScenarioError(f"{where}: {key} must be a positive whole number")
    return v


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path).resolve()
    try:
        raw = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise ScenarioError(f"scenario file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ScenarioError(f"{path}: {e}") from None
    where = path.name
    _keys(raw, TOP_KEYS, where)
    if not isinstance(raw.get("config"), str):
        raise ScenarioError(f"{where}: config = \"<robot config .toml>\" is required")
    try:
        speed = parse_speed(str(raw.get("speed", "max")))
    except ValueError as e:
        raise ScenarioError(f"{where}: {e}") from None
    req = raw.get("require_lockstep", True)
    if not isinstance(req, bool):
        raise ScenarioError(f"{where}: require_lockstep must be true or false")
    steps_raw = raw.get("step", [])
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ScenarioError(f"{where}: needs at least one [[step]]")
    steps = []
    for i, s in enumerate(steps_raw, 1):
        sw = f"{where}, step {i}"
        _keys(s, STEP_KEYS, sw)
        name = str(s.get("name", f"step {i}"))
        sw = f"{where}, step {i} ({name})" if "name" in s else sw
        if s.get("mode") not in MODES:
            raise ScenarioError(f"{sw}: mode must be one of {', '.join(MODES)}, not {s.get('mode')!r}")
        dur = s.get("duration")
        if isinstance(dur, bool) or not isinstance(dur, (int, float)) or dur <= 0:
            raise ScenarioError(f"{sw}: duration must be a positive number of seconds, not {dur!r}")
        checks = s.get("check", [])
        if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
            raise ScenarioError(f"{sw}: checks are [[step.check]] tables")
        for c in checks:  # structure only here; names are checked against the world in validate()
            kinds = [k for k in c]
            if len(kinds) != 1:
                raise ScenarioError(f"{sw}: each [[step.check]] needs exactly one of pose, odometry_error, at_end, "
                                    f"always, never_touch, must_touch, console; got {', '.join(kinds) or 'nothing'}")
        shots = []
        for j, sh in enumerate(s.get("screenshot", []), 1):
            shw = f"{sw}, screenshot {j}"
            _keys(sh, SHOT_KEYS, shw)
            at = sh.get("at", "end")
            if at != "end" and (isinstance(at, bool) or not isinstance(at, (int, float)) or at < 0):
                raise ScenarioError(f"{shw}: at must be \"end\" or seconds into the step")
            if at != "end" and at > dur:
                raise ScenarioError(f"{shw}: at = {at} is beyond the step's {dur:g} s")
            shots.append(Shot(str(sh.get("view", "follow")), at, _int(sh, "width", 960, shw),
                              _int(sh, "height", 600, shw), sh.get("camera")))
        sheet = None
        if "contact_sheet" in s:
            cs = s["contact_sheet"]
            _keys(cs, SHEET_KEYS, f"{sw}, contact_sheet")
            sheet = SheetSpec(_int(cs, "frames", 8, sw), str(cs.get("view", "follow")),
                              _int(cs, "width", 480, sw), _int(cs, "height", 300, sw))
            if sheet.frames > 16:
                raise ScenarioError(f"{sw}: contact_sheet frames must be 1-16")
        steps.append(Step(name, s["mode"], float(dur), _ports(s.get("axes", {}), sw, "axes"),
                          _ports(s.get("buttons", {}), sw, "buttons"), _ports(s.get("povs", {}), sw, "povs"),
                          checks, shots, sheet))
    return Scenario(path, (path.parent / raw["config"]).resolve(), str(raw.get("description", "")), speed, req, steps)


def validate(scenario: Scenario, world) -> None:
    """Check every name in the scenario against the world before anything runs."""
    for i, step in enumerate(scenario.steps, 1):
        where = f"{scenario.path.name}, step {i} ({step.name})"
        for c in step.checks:
            build_check(c, world, where)
        views = [s.view for s in step.screenshots] + ([step.contact_sheet.view] if step.contact_sheet else [])
        for v in views:
            if v not in ("follow", "top", "free") and not v.startswith("camera:"):
                raise ScenarioError(f"{where}: {VIEWS_HELP}, not {v!r}")
```

- [ ] **Step 6: Run the tests.** Run `.venv/bin/python -m pytest tests/test_scenario.py -v`. Expected: all pass. If `test_contact_checks` doesn't register contact at `x = 8.27 - 0.9`, print `world.robot_contacts()` after `set_robot_pose` and adjust the offset so the front bumper overlaps the obstacle (obstacle half-size 0.6, bumper front at +0.41). Note the value you used in the report.

- [ ] **Step 7: Commit.**

```bash
git add scrimmage/scenario.py scrimmage/checks.py scrimmage/world.py tests/test_scenario.py
git commit -m "Add scenario files: loading, validation with helpful errors, signals and checks"
```

---

### Task 11: Scenario runner, reports, the new CLI, and the migration off `DSScript`

**Files:**
- Create: `scrimmage/scenario_runner.py`, `scenarios/kitbot_auto.toml`, `tests/scenarios/kitbot_auto_wrong_target.toml`, `tests/probe.toml`, `tests/test_runner.py`
- Rewrite: `scrimmage/__main__.py`, `tests/test_e2e.py`
- Modify:
  - `scrimmage/bridge.py` (remove `DSScript`, `load_ds_script`, `ds_script`, `exit_after_script`)
  - `scrimmage/robot_process.py` (nothing beyond `echo`, which already exists)
  - `configs/kitbot.toml`, `tests/can_robot.toml` (add `[robot_program]`)
  - `tests/lockstep_probe/robot.py` (`PROBE_CRASH_AT_LOOP`)
  - `tests/test_bridge.py` (drop DS-script uses)
  - `.gitignore` (`runs/`)

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `NT_SETTLE = 0.25`
  - `Report` with `passed`, `exit_code`, `error`, `steps`, `implicit`, `to_dict()` and `write(out_dir)`
  - `async run_scenario(scenario, out_dir, *, speed=UNSET, session=None) -> Report`
  - the CLI `scrimmage serve CONFIG [--launch] [--viewer] [--speed X] [--step S] [--log F] [--port P] [--host H] [--no-nt] [-v]`
  - the CLI `scrimmage run SCENARIO [--out DIR] [--speed X] [-v]`

- [ ] **Step 1: Add the configs and scenarios.**

Append to `configs/kitbot.toml`, after `[lockstep]`:

```toml
[robot_program]
# How scrimmage starts the robot program ("{python}" = the Python running scrimmage).
command = ["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]
cwd = "../examples/robotpy_kitbot"

[telemetry]
odometry = "/SmartDashboard/Field/Robot"   # NT topic with the robot's own pose estimate (Field2d)
```

Append to `tests/can_robot.toml`:

```toml
[robot_program]
command = ["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client"]
cwd = "can_robot"
```

`tests/probe.toml`: copy `tests/fake_robot.toml`, but use this `[robot_program]`:

```toml
[robot_program]
command = ["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]
cwd = "lockstep_probe"
env = { PROBE_CRASH_AT_LOOP = "10" }
```

In `tests/lockstep_probe/robot.py`, add as the first line of `robotPeriodic`:

```python
        if self.loops.get() + 1 == int(os.environ.get("PROBE_CRASH_AT_LOOP", "0")):
            raise RuntimeError("probe robot crashed on purpose")
```

`scenarios/kitbot_auto.toml`:

```toml
# The kitbot's autonomous routine: drive 2 m, turn 90° left, drive 1 m.
#   scrimmage run scenarios/kitbot_auto.toml
config = "../configs/kitbot.toml"
description = "Kitbot auto ends 2 m ahead and 1 m left of its start, facing left"

[[step]]
mode = "disabled"
duration = 2.0

[[step]]
name = "auto"
mode = "auto"
duration = 9.0
  [[step.check]]
  pose = { x = 4.0, y = 5.035, heading = 90, tol_xy = 0.3, tol_heading = 4 }
  [[step.check]]
  always = { signal = "battery", min = 7.0 }
  [[step.check]]
  never_touch = "obstacle"
  [[step.check]]
  console = { contains = "step 3: stop" }
  [[step.screenshot]]
  view = "top"
  at = "end"
  [step.contact_sheet]
  frames = 8
  view = "follow"

[[step]]
mode = "disabled"
duration = 0.5
```

`tests/scenarios/kitbot_auto_wrong_target.toml`: the same content, with `config = "../../configs/kitbot.toml"`, the pose check `pose = { x = 6.0, y = 5.035, tol_xy = 0.3 }`, and no screenshot or contact sheet.

- [ ] **Step 2: Write the failing tests.** `tests/test_runner.py` (fast; uses the fake robot):

```python
import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

import scrimmage.session as session_mod
from scrimmage.render import RenderUnavailable
from scrimmage.scenario import load_scenario
from scrimmage.scenario_runner import run_scenario

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent


def scenario(tmp_path, body: str) -> Path:
    p = tmp_path / "s.toml"
    p.write_text(f'config = "{TESTS / "fake_robot.toml"}"\n' + body)
    return p


SIMPLE = """
[[step]]
mode = "auto"
duration = 0.5
  [[step.check]]
  pose = { x = 2.0, y = 4.035, tol_xy = 0.05 }
  [[step.check]]
  always = { signal = "battery", min = 12.0 }
  [[step.screenshot]]
  view = "top"
  at = 0.25
  width = 320
  height = 200
"""


def test_runner_passes_and_writes_report(tmp_path):
    out = tmp_path / "out"
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, SIMPLE)), out))
    assert rep.exit_code == 0, rep.to_dict()
    data = json.loads((out / "report.json").read_text())
    assert data["passed"] is True and len(data["steps"][0]["checks"]) == 2
    shot = out / data["steps"][0]["screenshots"][0]
    assert shot.read_bytes()[:4] == b"\x89PNG"
    md = (out / "report.md").read_text()
    assert "PASS" in md and shot.name in md
    assert (out / "run.csv").exists() and (out / "robot.log").exists()


def test_runner_failure_has_measured_values(tmp_path):
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, SIMPLE.replace("x = 2.0", "x = 3.0"))),
                                   tmp_path / "out"))
    assert rep.exit_code == 1
    pose = rep.steps[0].checks[0]
    assert not pose.passed and "1.00 m from target" in pose.message


def test_runner_crash_is_a_failed_check(tmp_path):
    body = SIMPLE
    p = scenario(tmp_path, body)
    cfg = tmp_path / "crash.toml"
    cfg.write_text((TESTS / "fake_robot.toml").read_text()
                   .replace('"../models/', f'"{ROOT / "models"}/')
                   .replace('"--lockstep"]', '"--lockstep", "--exit-after", "20"]')
                   .replace('cwd = "."', f'cwd = "{TESTS}"'))
    p.write_text(p.read_text().replace(str(TESTS / "fake_robot.toml"), str(cfg)))
    rep = asyncio.run(run_scenario(load_scenario(p), tmp_path / "out"))
    assert rep.exit_code == 1
    crash = [c for c in rep.implicit if not c.passed]
    assert crash and "exited" in crash[0].message


def test_runner_marks_screenshots_skipped_when_render_unavailable(tmp_path, monkeypatch):
    def unavailable(self, *a, **k):
        raise session_mod.SessionError("headless rendering isn't available (no GL). On Linux set MUJOCO_GL=egl")
    monkeypatch.setattr(session_mod.Session, "render", unavailable)
    rep = asyncio.run(run_scenario(load_scenario(scenario(tmp_path, SIMPLE)), tmp_path / "out"))
    assert rep.exit_code == 0
    assert rep.steps[0].skipped and "MUJOCO_GL" in rep.steps[0].skipped[0]


def test_lockstep_required_but_missing_is_exit_2(tmp_path, monkeypatch):
    import scrimmage.bridge as bridge_mod
    monkeypatch.setattr(bridge_mod, "DETECT_WINDOW", 0.2)
    cfg = tmp_path / "rt.toml"
    cfg.write_text((TESTS / "fake_robot.toml").read_text()
                   .replace('"../models/', f'"{ROOT / "models"}/')
                   .replace(', "--lockstep"]', "]").replace('cwd = "."', f'cwd = "{TESTS}"'))
    p = scenario(tmp_path, SIMPLE)
    p.write_text(p.read_text().replace(str(TESTS / "fake_robot.toml"), str(cfg)))
    rep = asyncio.run(run_scenario(load_scenario(p), tmp_path / "out"))
    assert rep.exit_code == 2 and "lockstep" in rep.error


def test_cli_run_rejects_invalid_scenario(tmp_path):
    p = scenario(tmp_path, '[[step]]\nmode = "auto"\nduration = 1\n[[step.check]]\nalways = { signal = "volts", min = 1 }\n')
    out = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(p), "--out", str(tmp_path / "o")],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 2 and "battery, speed" in out.stderr


def test_cli_run_fake_robot(tmp_path):
    out = subprocess.run([sys.executable, "-m", "scrimmage", "run", str(scenario(tmp_path, SIMPLE)),
                          "--out", str(tmp_path / "o")], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "PASS" in out.stdout
```

- [ ] **Step 3: Confirm they fail.** Run `.venv/bin/python -m pytest tests/test_runner.py -v`. Expected: `ModuleNotFoundError: scrimmage.scenario_runner`.

- [ ] **Step 4: Implement `scrimmage/scenario_runner.py`.**

```python
"""Runs a scenario on a Session: each step's DS inputs, checks, screenshots and contact sheets, then a report."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from .checks import CRASH_PATTERN, Check, CheckResult, build_check
from .config import ConfigError, load_config
from .render import contact_sheet, to_png
from .scenario import Scenario, ScenarioError, validate
from .session import Session, SessionError

NT_SETTLE = 0.25   # wall seconds the simulation is held so the robot's NetworkTables can catch up
UNSET = object()


@dataclass
class StepReport:
    name: str
    mode: str
    start_t: float
    end_t: float = 0.0
    checks: list[CheckResult] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"name": self.name, "mode": self.mode, "start_t": self.start_t, "end_t": self.end_t,
                "passed": all(c.passed for c in self.checks), "checks": [c.to_dict() for c in self.checks],
                "screenshots": self.screenshots, "skipped": self.skipped}


@dataclass
class Report:
    scenario: str
    description: str
    steps: list[StepReport] = field(default_factory=list)
    implicit: list[CheckResult] = field(default_factory=list)
    error: str | None = None
    lockstep: bool = False
    console_tail: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.error is None and all(c.passed for s in self.steps for c in s.checks) and \
            all(c.passed for c in self.implicit)

    @property
    def exit_code(self) -> int:
        return 2 if self.error is not None else (0 if self.passed else 1)

    def to_dict(self) -> dict:
        return {"scenario": self.scenario, "description": self.description, "passed": self.passed,
                "exit_code": self.exit_code, "error": self.error, "lockstep": self.lockstep,
                "steps": [s.to_dict() for s in self.steps], "implicit": [c.to_dict() for c in self.implicit],
                "console_tail": self.console_tail, "files": self.files}

    def summary(self) -> str:
        if self.error is not None:
            return f"ERROR  {self.scenario}: {self.error}"
        lines = [f"{'PASS' if self.passed else 'FAIL'}  {self.scenario}"]
        for s in self.steps:
            for c in s.checks:
                lines.append(f"  {'ok  ' if c.passed else 'FAIL'} [{s.name}] {c.message}")
        for c in self.implicit:
            if not c.passed:
                lines.append(f"  FAIL {c.message}")
        return "\n".join(lines)

    def write(self, out_dir: Path) -> None:
        (out_dir / "report.json").write_text(json.dumps(self.to_dict(), indent=2, default=str))
        md = [f"# {'PASS' if self.passed else ('ERROR' if self.error else 'FAIL')}: {self.scenario}", ""]
        if self.description:
            md += [self.description, ""]
        if self.error:
            md += [f"**Couldn't run:** {self.error}", ""]
        for s in self.steps:
            md += [f"## {s.name} ({s.mode}, t={s.start_t:.2f}–{s.end_t:.2f} s)", ""]
            md += [f"- {'✅' if c.passed else '❌'} {c.message}" for c in s.checks]
            md += [f"- skipped: {x}" for x in s.skipped]
            md += [""] + [f"![{p}]({p})" for p in s.screenshots] + [""]
        bad = [c for c in self.implicit if not c.passed]
        if bad:
            md += ["## Robot program", ""] + [f"- ❌ {c.message}" for c in bad] + [""]
        if self.console_tail:
            md += ["## Console (last lines)", "", "```", *self.console_tail, "```", ""]
        (out_dir / "report.md").write_text("\n".join(md))


async def run_scenario(scenario: Scenario, out_dir: Path, *, speed=UNSET, session: Session | None = None) -> Report:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = Report(scenario.path.name, scenario.description,
                    files={"csv": "run.csv", "robot_log": "robot.log", "report": "report.json"})
    own = session is None
    try:
        if own:
            config = load_config(scenario.config_path)
            session = Session(config, speed=scenario.speed if speed is UNSET else speed,
                              log_path=out_dir / "run.csv", robot_log=out_dir / "robot.log")
            from .world import World
            validate(scenario, World(config.raw, config.dir))
            await session.start()
        else:
            if session.config.path != scenario.config_path:
                raise ScenarioError(f"this session runs {session.config.path}, but the scenario uses "
                                    f"{scenario.config_path}; start a session with that config")
            validate(scenario, session.world)
            await session.restart_robot()
    except (ScenarioError, ConfigError) as e:
        report.error = str(e)
        report.write(out_dir)
        return report
    except SessionError as e:
        report.error = str(e)
        report.console_tail = e.console
        if own:
            await session.stop()
        report.write(out_dir)
        return report

    try:
        report.lockstep = session.lockstep
        if scenario.require_lockstep and not session.lockstep:
            report.error = ("lockstep is required but the robot program didn't load the scrimmage_lockstep extension "
                            "(RobotPy: add --scrimmage-lockstep to [robot_program] command; or set "
                            "require_lockstep = false)")
            return report
        try:
            for i, step in enumerate(scenario.steps, 1):
                report.steps.append(await _run_step(session, step, i, out_dir))
        except SessionError as e:
            report.implicit.append(CheckResult("robot program running", False, f"{e} (t={e.t})"))
        _implicit(session, report)
        report.console_tail = session.robot.tail(50) if session.robot is not None else []
        return report
    finally:
        if own:
            await session.stop()
        report.write(out_dir)


async def _run_step(session: Session, step, index: int, out_dir: Path) -> StepReport:
    rep = StepReport(step.name, step.mode, round(session.t, 4))
    checks: list[Check] = [build_check(c, session.world, step.name) for c in step.checks]
    session.bridge.ds.joysticks.clear()
    for port, axes in step.axes.items():
        session.set_joystick(port, axes=axes)
    for port, buttons in step.buttons.items():
        session.set_joystick(port, buttons=buttons)
    for port, povs in step.povs.items():
        session.set_joystick(port, povs=povs)
    session.set_mode(step.mode)
    for c in checks:
        c.begin(session)
    tick_now = [c for c in checks if c.ticks and not c.uses_nt]
    tick_nt = [c for c in checks if c.ticks and c.uses_nt]

    def on_step(t: float) -> None:
        for c in tick_now:
            c.tick(session, t)

    events: dict[float, list] = {}
    for n, shot in enumerate(step.screenshots, 1):
        at = step.duration if shot.at == "end" else float(shot.at)
        events.setdefault(round(at, 6), []).append(("shot", n, shot))
    sheet_frames = []
    if step.contact_sheet is not None:
        for k in range(step.contact_sheet.frames):
            events.setdefault(round(step.duration * (k + 1) / step.contact_sheet.frames, 6), []).append(("frame",))
    if tick_nt:
        k = 1
        while k * NT_SETTLE < step.duration:
            events.setdefault(round(k * NT_SETTLE, 6), []).append(("nt",))
            k += 1
    events.setdefault(round(step.duration, 6), [])

    start = session.t
    for target in sorted(events):
        dt = target - (session.t - start)
        if dt > 1e-9:
            await session.advance(dt, on_step=on_step)
        for ev in events[target]:
            if ev[0] == "nt":
                await asyncio.sleep(NT_SETTLE)
                for c in tick_nt:
                    c.tick(session, session.t)
            elif ev[0] == "shot":
                _, n, shot = ev
                name = f"step{index}-{n}-{shot.view.replace(':', '-')}.png"
                try:
                    png = session.screenshot(shot.view, width=shot.width, height=shot.height, camera=shot.camera)
                except SessionError as e:
                    rep.skipped.append(f"screenshot {name}: {e}")
                else:
                    (out_dir / name).write_bytes(png)
                    rep.screenshots.append(name)
            elif ev[0] == "frame":
                cs = step.contact_sheet
                try:
                    sheet_frames.append((session.t, session.render(cs.view, width=cs.width, height=cs.height)))
                except SessionError as e:
                    if not any("contact sheet" in s for s in rep.skipped):
                        rep.skipped.append(f"contact sheet: {e}")
    if any(c.uses_nt for c in checks):
        await asyncio.sleep(NT_SETTLE)
        for c in tick_nt:
            c.tick(session, session.t)
    rep.checks = [c.end(session) for c in checks]
    if sheet_frames:
        name = f"step{index}-contact-sheet.png"
        (out_dir / name).write_bytes(to_png(contact_sheet(sheet_frames)))
        rep.screenshots.append(name)
    rep.end_t = round(session.t, 4)
    return rep


def _implicit(session: Session, report: Report) -> None:
    if session.robot is not None:
        if session.robot.running:
            report.implicit.append(CheckResult("robot program running", True, "the robot program kept running"))
        elif not any(c.check == "robot program running" for c in report.implicit):
            report.implicit.append(CheckResult("robot program running", False,
                                               f"the robot program exited (code {session.robot.returncode})"))
        crash = next((l for l in session.robot.since(0) if CRASH_PATTERN.search(l.text)), None)
        report.implicit.append(CheckResult(
            "no crash in console", crash is None,
            "no stack trace in the console" if crash is None else
            f"stack trace in the console at t={crash.t:.3f}: {crash.text}"))
```

- [ ] **Step 5: Rewrite `scrimmage/__main__.py`.**

```python
"""scrimmage serve CONFIG [...]   |   scrimmage run SCENARIO [...]"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from .bridge import Bridge
from .config import ConfigError, load_config
from .lockstep import DEFAULT_STEP, parse_speed
from .robot_process import RobotProcess
from .scenario import ScenarioError, load_scenario, validate
from .scenario_runner import UNSET, run_scenario
from .world import World


def _speed(text: str) -> float | None:
    try:
        return parse_speed(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="scrimmage", description="Simulate a WPILib robot program in a MuJoCo world.")
    sub = ap.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("serve", help="run the world for a robot program you drive yourself (WPILib sim GUI)")
    sp.add_argument("config", type=Path, help="robot config TOML (model, device bindings, [robot_program])")
    sp.add_argument("--launch", action="store_true", help="start the robot program from [robot_program]")
    sp.add_argument("--viewer", action="store_true", help="open the MuJoCo 3D viewer")
    sp.add_argument("--speed", type=_speed, default=1.0,
                    help='real-time factor, or "max" (needs the lockstep extension unless 1; default 1)')
    sp.add_argument("--step", type=float, help=f"lockstep step in seconds (default: [lockstep] step, else {DEFAULT_STEP})")
    sp.add_argument("--log", type=Path, help="write a CSV of ground truth, odometry, motor commands and currents")
    sp.add_argument("--host", default="localhost")
    sp.add_argument("--port", type=int, default=3300)
    sp.add_argument("--no-nt", action="store_true", help="don't use NetworkTables")
    sp.add_argument("-v", "--verbose", action="store_true")

    rp = sub.add_parser("run", help="run a scenario file and write a report (exit 0 pass, 1 fail, 2 couldn't run)")
    rp.add_argument("scenario", type=Path)
    rp.add_argument("--out", type=Path, help="report directory (default runs/<scenario>-<timestamp>)")
    rp.add_argument("--speed", type=_speed, default=UNSET, help='override the scenario\'s speed ("max" or a factor)')
    rp.add_argument("-v", "--verbose", action="store_true")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    if args.command == "serve":
        _serve(args, sp)
    else:
        sys.exit(_run(args, rp))


def _serve(args, ap) -> None:
    try:
        config = load_config(args.config)
        world = World(config.raw, config.dir)
        bridge = Bridge(world, args.host, args.port, log_path=args.log, viewer=args.viewer, nt=not args.no_nt,
                        speed=args.speed, step=args.step if args.step is not None else config.step,
                        odometry_topic=config.odometry_topic, nt_port=config.nt_port)
    except (ConfigError, ValueError) as e:
        ap.error(str(e))
    if args.launch and config.robot_program is None:
        ap.error("--launch needs a [robot_program] section in the config")

    async def go() -> None:
        task = asyncio.create_task(bridge.run())
        robot = None
        try:
            if args.launch:
                await bridge.listening.wait()
                p = config.robot_program
                robot = RobotProcess(p.command, p.cwd, p.env, clock=lambda: world.time, echo=True)
                await robot.start(bridge.bound_port)
            await task
        finally:
            if robot is not None:
                await robot.stop()

    try:
        asyncio.run(go())
    except KeyboardInterrupt:
        pass
    except RuntimeError as e:
        print(f"scrimmage: {e}", file=sys.stderr)
        sys.exit(2)
    sys.exit(bridge.exit_code)


def _run(args, ap) -> int:
    try:
        scenario = load_scenario(args.scenario)
        config = load_config(scenario.config_path)
        validate(scenario, World(config.raw, config.dir))
    except (ScenarioError, ConfigError, ValueError) as e:
        ap.error(str(e))
    out = args.out or Path("runs") / f"{scenario.path.stem}-{datetime.now():%Y%m%d-%H%M%S}"
    report = asyncio.run(run_scenario(scenario, out, speed=args.speed))
    print(report.summary())
    print(f"report: {out / 'report.md'}")
    return report.exit_code


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Remove `DSScript` from `scrimmage/bridge.py`.**
  - Delete the `DSScript` class, `load_ds_script`, the `ds_script` and `exit_after_script` constructor parameters and attributes, and the `if self.ds_script is not None:` block in `_on_tick`. `_on_tick`'s DS part becomes:

```python
        mode = self.ds.mode if self.ds.active else "external"
        if self.ds.active and (self.ds.changed or now - t.ds >= DS_PERIOD):
            t.ds = now
            self.ds.changed = False
            await self._send(self.ds.frames())
```

  - Remove the now-unused `json` and `Joystick` imports.

In `tests/test_bridge.py`:
- Delete `test_ds_script_runs_on_simulated_time`. It's covered by `test_bridge_sends_ds_state_and_mode_changes_immediately` and the runner tests.
- In `test_lockstep_max_speed_beats_real_time` and `test_lockstep_logs_rate_summary`:
  - replace the `ds_script=..., exit_after_script=True` arguments with nothing
  - stop the run with `await wait_until(lambda: bridge.world.time >= 1.5, 10); bridge.done.set()` (use `0.2` for the rate-summary test)
  - in the max-speed test, set `bridge.ds.active = True; bridge.ds.set_mode("auto")` before connecting, and keep its `True in enabled` assertion
- Remove the `load_ds_script` import.

- [ ] **Step 7: Rewrite `tests/test_e2e.py`** to use scenarios and `Session`:

```python
"""End-to-end: real RobotPy robot programs through `scrimmage run` and Session.

Needs robotpy[sim], robotpy-halsim-ws and the lockstep extension (pip install ./lockstep).
"""

import asyncio
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("wpilib")
pytest.importorskip("halsim_ws")

from scrimmage.session import Session, SessionError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def cli(*args, timeout=180):
    return subprocess.run([sys.executable, "-m", "scrimmage", *map(str, args)], cwd=ROOT,
                          capture_output=True, text=True, timeout=timeout)


def variant(tmp_path, src: Path, *replacements) -> Path:
    """A copy of a config with absolute paths, so it can live in tmp_path, plus text replacements."""
    text = src.read_text().replace('"../models/', f'"{ROOT / "models"}/')
    text = text.replace('cwd = "../examples/robotpy_kitbot"', f'cwd = "{ROOT / "examples" / "robotpy_kitbot"}"')
    for old, new in replacements:
        text = text.replace(old, new)
    p = tmp_path / src.name
    p.write_text(text)
    return p


def test_kitbot_auto_scenario_passes(tmp_path):
    pytest.importorskip("scrimmage_lockstep")
    out = tmp_path / "out"
    r = cli("run", ROOT / "scenarios" / "kitbot_auto.toml", "--out", out)
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    data = json.loads((out / "report.json").read_text())
    assert data["passed"] and data["lockstep"]
    shots = data["steps"][1]["screenshots"]
    assert any("top" in s for s in shots) and any("contact-sheet" in s for s in shots)
    assert all((out / s).read_bytes()[:4] == b"\x89PNG" for s in shots)
    m = re.search(r"\(([\d.]+)x real time\)", r.stderr)
    assert m and float(m.group(1)) >= 2.0, r.stderr[-2000:]
    print(f"lockstep rate {m.group(1)}x")


def test_wrong_target_fails_with_measured_values(tmp_path):
    pytest.importorskip("scrimmage_lockstep")
    out = tmp_path / "out"
    r = cli("run", ROOT / "tests" / "scenarios" / "kitbot_auto_wrong_target.toml", "--out", out)
    assert r.returncode == 1, r.stdout + r.stderr[-2000:]
    pose = json.loads((out / "report.json").read_text())["steps"][1]["checks"][0]
    assert not pose["passed"] and "m from target" in pose["message"] and pose["measured"]["x"] < 5


def test_runs_are_repeatable(tmp_path):
    pytest.importorskip("scrimmage_lockstep")
    logs = []
    for i in range(2):
        out = tmp_path / f"out{i}"
        r = cli("run", ROOT / "tests" / "scenarios" / "kitbot_auto_wrong_target.toml", "--out", out)
        assert r.returncode == 1, r.stderr[-2000:]  # fails its pose check on purpose; runs still repeat
        rows = list(csv.DictReader(open(out / "run.csv")))
        logs.append([{k: v for k, v in row.items() if not k.startswith("odo_")} for row in rows])
    assert len(logs[0]) > 200 and logs[0] == logs[1]


def test_realtime_scenario_without_extension(tmp_path):
    cfg = variant(tmp_path, ROOT / "configs" / "kitbot.toml", (', "--scrimmage-lockstep"]', "]"))
    sc = tmp_path / "rt.toml"
    sc.write_text((ROOT / "scenarios" / "kitbot_auto.toml").read_text()
                  .replace('config = "../configs/kitbot.toml"', f'config = "{cfg}"\nspeed = 1\nrequire_lockstep = false'))
    r = cli("run", sc, "--out", tmp_path / "out")
    assert r.returncode == 0, r.stdout + r.stderr[-3000:]
    assert "behind real time" not in r.stderr


def test_can_shim_round_trip_via_session():
    async def main():
        s = Session(ROOT / "tests" / "can_robot.toml", speed=1.0, nt=False)
        try:
            await s.start()
            await s.advance(1.5)
            s.set_mode("auto")
            await s.advance(2.5)
            s.set_mode("disabled")
            await s.advance(0.5)
            text = "\n".join(line["text"] for line in s.console()["lines"])
            state = s.state()
        finally:
            await s.stop()
        lines = re.findall(r"CAN rot=([-\d.]+),([-\d.]+) rps=([-\d.]+),([-\d.]+) vbus=([\d.]+)", text)
        io = re.findall(r"dio=(True|False) ai=([\d.]+)", text)
        assert io[0] == ("False", "0.000") and io[-1][0] == "True" and 0.0 < float(io[-1][1]) <= 5.0
        assert lines, text[-2000:]
        l_rot, r_rot, l_rps, r_rps, vbus = map(float, lines[-1])
        assert l_rot > 20 and r_rot < -20 and l_rps == pytest.approx(-r_rps, rel=0.02) and 11.0 < vbus <= 12.5
        assert state["pose"]["x"] > 4.0 and abs(state["pose"]["heading_deg"]) < 3
    asyncio.run(main())


def test_session_on_real_kitbot_reads_nt_and_screenshots():
    pytest.importorskip("scrimmage_lockstep")
    pytest.importorskip("ntcore")

    async def main():
        s = Session(ROOT / "configs" / "kitbot.toml")
        try:
            st = await s.start()
            assert st["lockstep"]
            s.set_mode("auto")
            r = await s.advance(3.0)
            assert r["pose"]["x"] > 2.5
            await asyncio.sleep(0.3)  # let NT flush
            gyro = s.nt_get(["/SmartDashboard/Gyro deg"])["values"]
            assert "/SmartDashboard/Gyro deg" in gyro
            assert s.screenshot("top", width=480, height=300)[:4] == b"\x89PNG"
        finally:
            await s.stop()
    asyncio.run(main())


def test_session_restart_after_probe_crash():
    pytest.importorskip("scrimmage_lockstep")

    async def main():
        s = Session(ROOT / "tests" / "probe.toml", nt=False)
        try:
            await s.start()
            with pytest.raises(SessionError, match="exited") as e:
                await s.advance(1.0)                 # loop 10 (t=0.2 s) raises
            assert any("Traceback" in l for l in e.value.console)
            st = await s.restart_robot()
            assert st["lockstep"] and st["t"] == 0.0
            assert (await s.advance(0.1))["t"] == pytest.approx(0.1)
        finally:
            await s.stop()
    asyncio.run(main())


def test_cli_errors(tmp_path):
    bad_step = variant(tmp_path, ROOT / "configs" / "kitbot.toml", ("step = 0.005", 'step = "fast"'))
    kitbot = ROOT / "configs" / "kitbot.toml"
    for args, msg in [(["serve", kitbot, "--speed", "0"], "speed must be a positive number"),
                      (["serve", kitbot, "--step", "0.0025"], "whole multiple of the physics timestep"),
                      (["serve", bad_step], "[lockstep] step must be a number"),
                      (["run", tmp_path / "missing.toml"], "scenario file not found"),
                      ([kitbot], "invalid choice")]:
        r = cli(*args, timeout=30)
        assert r.returncode == 2, args
        assert msg in r.stderr, (args, r.stderr)
```

Add `runs/` to `.gitignore`.

- [ ] **Step 8: Run the whole suite.** Run `.venv/bin/python -m pytest -v -rs`.
  - Expected: all pass, 0 skipped.
  - If `test_session_restart_after_probe_crash` sees the robot still running after its `RuntimeError`, it's because RobotPy may log the exception and keep going. In that case:
    - change the probe to call `os._exit(3)` after printing a traceback (`traceback.print_stack()`, then `print("RuntimeError: probe robot crashed on purpose", flush=True)`)
    - note the change in the report

- [ ] **Step 9: Commit.**

```bash
git add -A scrimmage tests scenarios configs .gitignore
git commit -m "Add the scenario runner, reports and the serve/run CLI; replace DSScript with scenarios"
```

---

### Task 12: MCP server

**Files:**
- Create: `scrimmage/mcp_server.py`, `tests/test_mcp.py`
- Modify: `pyproject.toml` (`mcp` extra, `dev`, the `scrimmage-mcp` script)

**Interfaces:**
- Consumes: `Session` (all methods), `load_scenario`, `run_scenario`.
- Produces:
  - `server: MCPServer`
  - `main()`
  - the console script `scrimmage-mcp`
  - the tools `start_session`, `stop_session`, `restart_robot`, `session_status`, `advance`, `set_mode`, `set_joystick`, `get_state`, `get_hal`, `get_console`, `nt_list`, `nt_get`, `set_pose`, `push`, `move_body`, `screenshot`, `contact_sheet` and `run_scenario`

- [ ] **Step 1: Add the dependency.** In `pyproject.toml`:
  - add `mcp = ["mcp>=2.3,<3"]` to `[project.optional-dependencies]`
  - add `"mcp>=2.3,<3"` and `"pillow"` to `dev`
  - add `scrimmage-mcp = "scrimmage.mcp_server:main"` under `[project.scripts]`

  Then run `.venv/bin/pip install -e ".[dev,nt,mcp]"`.

- [ ] **Step 2: Write the failing tests.** `tests/test_mcp.py`:

```python
import asyncio
import base64
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402
from mcp.client.stdio import StdioServerParameters  # noqa: E402

from scrimmage import mcp_server  # noqa: E402

FAKE = str(Path(__file__).resolve().parent / "fake_robot.toml")
TOOLS = {"start_session", "stop_session", "restart_robot", "session_status", "advance", "set_mode", "set_joystick",
         "get_state", "get_hal", "get_console", "nt_list", "nt_get", "set_pose", "push", "move_body", "screenshot",
         "contact_sheet", "run_scenario"}


def text(result) -> dict:
    return json.loads(result.content[0].text)


def test_tools_drive_a_session():
    async def main():
        async with Client(mcp_server.server) as c:
            names = {t.name for t in (await c.list_tools()).tools}
            assert TOOLS <= names
            try:
                r = await c.call_tool("start_session", {"config": FAKE})
                assert not r.is_error and text(r)["lockstep"] is True
                r = await c.call_tool("start_session", {"config": FAKE})
                assert r.is_error and "replace" in r.content[0].text
                assert text(await c.call_tool("set_mode", {"mode": "teleop"}))["mode"] == "teleop"
                assert text(await c.call_tool("advance", {"seconds": 0.1}))["t"] == pytest.approx(0.1)
                r = await c.call_tool("advance", {"seconds": 61})
                assert r.is_error and "60" in r.content[0].text
                shot = await c.call_tool("screenshot", {"view": "top", "width": 320, "height": 200})
                img = [b for b in shot.content if b.type == "image"][0]
                assert img.mime_type == "image/png" and base64.b64decode(img.data)[:4] == b"\x89PNG"
                r = await c.call_tool("set_mode", {"mode": "autonomous"})
                assert r.is_error and "teleop" in r.content[0].text
                assert text(await c.call_tool("set_pose", {"x": 3.0, "y": 3.0, "heading_deg": 0}))["pose"]["x"] == 3.0
            finally:
                await c.call_tool("stop_session", {})
            r = await c.call_tool("advance", {"seconds": 0.1})
            assert r.is_error and "start_session" in r.content[0].text
    asyncio.run(main())


def test_stdio_server_lists_tools():
    async def main():
        params = StdioServerParameters(command=sys.executable, args=["-m", "scrimmage.mcp_server"])
        async with Client(params) as c:
            assert TOOLS <= {t.name for t in (await c.list_tools()).tools}
    asyncio.run(main())
```

- [ ] **Step 3: Confirm they fail.** Run `.venv/bin/python -m pytest tests/test_mcp.py -v`. Expected: `ImportError: cannot import name 'mcp_server'`.

- [ ] **Step 4: Implement `scrimmage/mcp_server.py`.**

```python
"""MCP server: lets an agent run a robot program in scrimmage and "step, look, poke".

Run as `scrimmage-mcp` (stdio). Register it with an MCP client, e.g. Claude Code:
    claude mcp add scrimmage -- /path/to/.venv/bin/scrimmage-mcp
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .config import ConfigError
from .lockstep import parse_speed
from .scenario import ScenarioError, load_scenario
from .scenario_runner import run_scenario as _run_scenario
from .session import Session, SessionError

MAX_ADVANCE = 60.0
MAX_IMAGES = 6

INSTRUCTIONS = """\
scrimmage simulates a WPILib (FRC) robot program in a physics world. Typical loop:
start_session(config=<robot config .toml>) -> set_mode("auto") -> advance(seconds) -> screenshot("top") / get_state()
-> edit robot code -> restart_robot() -> repeat. The simulation is held (paused, robot code included) between calls,
so take your time. Every result includes t, the simulated time in seconds. Pokes (set_pose, push, move_body) change
physics only; the robot's own odometry isn't told. run_scenario runs a scenario file's checks and returns its report.
"""

server = MCPServer(name="scrimmage", instructions=INSTRUCTIONS)
_session: Session | None = None


def _json(d: Any) -> str:
    return json.dumps(d, separators=(",", ":"), default=str)


def _fail(e: Exception) -> ToolError:
    if isinstance(e, SessionError):
        return ToolError(_json(e.to_dict()))
    return ToolError(str(e))


def _need() -> Session:
    if _session is None:
        raise ToolError("no session: call start_session(config=...) first")
    return _session


@server.tool()
async def start_session(config: str, attach: bool = False, viewer: bool = False, replace: bool = False,
                        speed: str = "max") -> str:
    """Start simulating a robot program. config: path to a robot config TOML (model, bindings, [robot_program]).
    attach: wait for a robot program you start yourself instead of launching one. viewer: open a 3D window.
    speed: "max" (default) or a real-time factor, used while advancing. The session starts held at t=0, disabled."""
    global _session
    if _session is not None:
        if not replace:
            raise ToolError("a session is already running: call stop_session first, or pass replace=true")
        await _session.stop()
        _session = None
    try:
        s = Session(Path(config).expanduser(), attach=attach, viewer=viewer, speed=parse_speed(speed))
        result = await s.start()
    except (SessionError, ConfigError, ValueError) as e:
        raise _fail(e) from None
    _session = s
    return _json(result)


@server.tool()
async def stop_session() -> str:
    """Stop the session and the robot program."""
    global _session
    if _session is not None:
        await _session.stop()
        _session = None
    return _json({"stopped": True})


@server.tool()
async def restart_robot() -> str:
    """Restart the robot program (e.g. after editing its code) and reset the world to t=0, disabled."""
    try:
        return _json(await _need().restart_robot())
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def session_status() -> str:
    """Whether a session is running, lockstep is active, and which HAL devices the robot program created."""
    return _json(_session.status() if _session is not None else {"session": None})


@server.tool()
async def advance(seconds: float) -> str:
    """Run the simulation forward by `seconds` (at most 60), then hold it again. Returns the state and any new
    robot console lines."""
    if not 0 < seconds <= MAX_ADVANCE:
        raise ToolError(f"seconds must be > 0 and at most {MAX_ADVANCE:g} per call")
    try:
        return _json(await _need().advance(seconds))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def set_mode(mode: str) -> str:
    """Set the Driver Station mode: disabled, auto, teleop or test. Takes effect on the next advance."""
    try:
        return _json(_need().set_mode(mode))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def set_joystick(port: int, axes: list[float] | None = None, buttons: list[bool] | None = None,
                       povs: list[int] | None = None) -> str:
    """Set a joystick (port 0-5): axes in [-1, 1], buttons (index 0 = button 1), POV angles (-1 = centered)."""
    try:
        return _json(_need().set_joystick(port, axes, buttons, povs))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def get_state() -> str:
    """Ground-truth pose (m, degrees CCW), speed, battery voltage, motor commands/currents, joint positions."""
    return _json(_need().state())


@server.tool()
async def get_hal(device_filter: str = "") -> str:
    """Raw HAL values the robot program reported, by "type/device"; device_filter matches a substring."""
    return _json(_need().hal(device_filter))


@server.tool()
async def get_console(since: int = 0) -> str:
    """Robot program console lines (with simulated timestamps) from line index `since`; returns `next`."""
    return _json(_need().console(since))


@server.tool()
async def nt_list(prefix: str = "/") -> str:
    """List the robot's NetworkTables topics under a prefix."""
    try:
        return _json(_need().nt_list(prefix))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def nt_get(topics: list[str]) -> str:
    """Read NetworkTables values (e.g. "/SmartDashboard/Gyro deg"). Values lag the simulation by up to ~0.1 s."""
    try:
        return _json(_need().nt_get(topics))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def set_pose(x: float, y: float, heading_deg: float) -> str:
    """Teleport the robot (physics only; the robot's odometry isn't reset)."""
    try:
        return _json(_need().set_pose(x, y, heading_deg))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def push(force_n: float, direction_deg: float, duration_s: float) -> str:
    """Push the robot with a horizontal force (N) toward direction_deg (field frame, CCW from +x) for duration_s
    of simulated time. This advances time."""
    try:
        return _json(await _need().push(force_n, direction_deg, duration_s))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def move_body(name: str, x: float, y: float, z: float | None = None, yaw_deg: float = 0.0) -> str:
    """Move an object in the world (a free body or a named static geom such as "obstacle")."""
    try:
        return _json(_need().move_body(name, x, y, z, yaw_deg))
    except SessionError as e:
        raise _fail(e) from None


@server.tool()
async def screenshot(view: str = "follow", width: int = 960, height: int = 600, paths: bool = True,
                     camera: dict | None = None) -> list:
    """A picture of the world. view: "follow" (camera behind the robot), "top" (whole field; ground-truth path in
    green, odometry in orange), "camera:<name>" (a camera in the model), or "free" (camera={lookat:[x,y,z],
    distance, azimuth, elevation})."""
    s = _need()
    try:
        png = s.screenshot(view, width=width, height=height, paths=paths, camera=camera)
    except SessionError as e:
        raise _fail(e) from None
    return [_json({"t": round(s.t, 4), "view": view}), Image(data=png, format="png")]


@server.tool()
async def contact_sheet(seconds: float, frames: int = 8, view: str = "follow") -> list:
    """Advance `seconds` (at most 60), capturing `frames` (1-16) pictures at equal steps, as one grid image."""
    if not 0 < seconds <= MAX_ADVANCE:
        raise ToolError(f"seconds must be > 0 and at most {MAX_ADVANCE:g} per call")
    s = _need()
    try:
        png = await s.contact_sheet(seconds, frames, view)
    except SessionError as e:
        raise _fail(e) from None
    return [_json(s.state()), Image(data=png, format="png")]


@server.tool()
async def run_scenario(path: str, out_dir: str | None = None) -> list:
    """Run a scenario file (steps, checks, screenshots). Uses the current session if its config matches (the robot
    is restarted), otherwise runs standalone. Returns the report and its screenshots."""
    try:
        sc = load_scenario(Path(path).expanduser())
    except ScenarioError as e:
        raise _fail(e) from None
    out = Path(out_dir).expanduser() if out_dir else Path("runs") / f"{sc.path.stem}-mcp"
    use = _session if _session is not None and _session.config.path == sc.config_path else None
    report = await _run_scenario(sc, out, session=use)
    result: list = [_json({**report.to_dict(), "out_dir": str(out.resolve())})]
    for step in report.steps:
        for name in step.screenshots:
            if len(result) > MAX_IMAGES:
                break
            result.append(Image(data=(out / name).read_bytes(), format="png"))
    return result


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run the tests.** Run `.venv/bin/python -m pytest tests/test_mcp.py -v`. Expected: 2 passed.
  - If `MCPServer.run()` needs an explicit transport, use `server.run("stdio")`.
  - If a tool returning `list` doesn't produce image content, check `_convert_to_content` in `mcp/server/mcpserver/utilities/func_metadata.py`. Lists of `str` and `Image` are supported there. Report any API adjustment you make.

- [ ] **Step 6: Run the full suite, then commit.** Run `.venv/bin/python -m pytest -q`; expect all to pass.

```bash
git add scrimmage/mcp_server.py tests/test_mcp.py pyproject.toml
git commit -m "Add the scrimmage MCP server: sessions, time, driver inputs, reads, pokes, screenshots, scenarios"
```

---

### Task 13: Docs and CI

**Files:**
- Modify: `README.md`, `.github/workflows/ci.yml`, `examples/java/README.md`

- [ ] **Step 1: CI.** In `.github/workflows/ci.yml`:
  - Before `Install`, add:

```yaml
      # headless rendering for screenshots (no GPU on CI): Mesa's software OSMesa
      - name: Install OSMesa
        run: sudo apt-get update && sudo apt-get install -y libosmesa6
```

  - Change the install line to `pip install -e ".[dev,nt,mcp]"`.
  - Add `env: { MUJOCO_GL: osmesa }` to the `Test` step.
  - Extend the import check to `python -c "import wpilib, halsim_ws, scrimmage_lockstep, mcp, ntcore"`.

- [ ] **Step 2: README.** Update `README.md`:
  - **Quick start:** replace the two-terminal flow with `scrimmage serve configs/kitbot.toml --launch --viewer`, which starts the robot program from `[robot_program]` and opens the viewer. Then drive it from the WPILib sim GUI as before. Keep a sentence on running the robot program yourself (`scrimmage serve configs/kitbot.toml` plus `python -m robotpy sim --ws-client --scrimmage-lockstep` in its directory).
  - **Headless runs:** replace with scenario files: `scrimmage run scenarios/kitbot_auto.toml`. Explain the exit codes (0/1/2), the report directory (`report.md`, `report.json`, screenshots, `run.csv`, `robot.log`), and that `--speed` overrides the scenario.
  - **A new section "Scenario files"**, after "Lockstep":
    - the step format, with the check vocabulary table and the signal names (copy the table from the spec's "Checks" section)
    - screenshots and contact sheets
    - the implicit checks
    - the NT timing note
  - **A new section "Agents (MCP)":**
    - What it's for: step, look, poke.
    - Install with `pip install -e ".[mcp]"`.
    - Register with Claude Code: `claude mcp add scrimmage -- $(pwd)/.venv/bin/scrimmage-mcp`.
    - The tool list in one table: tool and what it does.
    - Each config's `[robot_program]` is how agents launch the robot.
    - The `MUJOCO_GL` note: defaults to `egl` on Linux; use `osmesa` without a GPU.
  - **A new section "Python API":** a 10-line `Session` example (start, `set_mode`, `advance`, `state`, `screenshot`, `stop`).
  - **Configuration:** document `[robot_program]` (`command` with `"{python}"`, `cwd`, `env`, `start_timeout`), `[telemetry]` (`odometry`, `nt_port`) and `[session] step_timeout`.
  - **Remove** every mention of `--ds-script`, `--exit-after-script` and `python -m scrimmage CONFIG`.
  - **Limits:** add "one session per MCP server", and "NT values lag at `--speed max` (scenario checks hold to let them settle)".

In `examples/java/README.md`, replace `python -m scrimmage configs/your_robot.toml --viewer` with `scrimmage serve configs/your_robot.toml --viewer`.

- [ ] **Step 3: Check the docs against the code.** Run `.venv/bin/python -m scrimmage serve --help`, `.venv/bin/python -m scrimmage run --help` and `.venv/bin/scrimmage-mcp --help 2>&1 | head -5` (the last may just start the server; Ctrl+C is fine). Confirm that every flag in the README exists. Run `grep -n "ds-script\|exit-after-script\|python -m scrimmage configs" README.md examples/java/README.md`, which should print nothing.

- [ ] **Step 4: Run the full suite.** Run `MUJOCO_GL=osmesa .venv/bin/python -m pytest -v -rs` if `libosmesa6` is installed locally, otherwise plain `.venv/bin/python -m pytest -v -rs`. Expected: all pass, 0 skipped.

- [ ] **Step 5: Commit.**

```bash
git add README.md examples/java/README.md .github/workflows/ci.yml
git commit -m "Document serve/run, scenario files, the MCP server and the Python API; CI renders with OSMesa"
```
