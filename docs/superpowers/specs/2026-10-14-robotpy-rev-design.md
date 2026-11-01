# RobotPy REV support (sub-project 6b): design

## Context

Sub-project 6 (merged 2026-10-14) added REV SPARK MAX / SPARK Flex support for Java robots: the Scrimmage Java agent
registers each SPARK (`ScrimmageRev`) and, every robot loop, drives REV's own `SparkSim.iterate` with Scrimmage's
physics values through a helper HAL SimDevice per SPARK, `ScrimmageREV:SparkMax[<id>]` / `ScrimmageREV:SparkFlex[<id>]`
(inputs `velocity`, `position`, `vbus`, `current`, `absRotations`, `fwdLimit`, `revLimit`, `motor` code; output
`motorVoltage`). Scrimmage's side (bindings, bridge checks, the "REV simulation available" scenario check, docs/rev.md)
is language-neutral.

A spike (2026-10-14, throwaway venv) found:

- `robotpy-rev` 2026.0.4 (RobotPy pieces Apache-2.0, REV pieces BSD-3-Clause) exposes everything the Java helper
  uses: `SparkMaxSim` / `SparkFlexSim` (`iterate`, `setPosition`, `setMotorCurrent`, `setAppliedOutput`,
  `getAppliedOutput`, `getAbsoluteEncoderSim`, `getForward/ReverseLimitSwitchSim`, `getRelativeEncoderSim`),
  `SparkAbsoluteEncoderSim`, `SparkLimitSwitchSim`, `configAccessor` (`getInverted`, `getFollowerModeLeaderId`,
  `getFollowerModeInverted`, `encoder`, `absoluteEncoder`, `closedLoop`), `SparkMax.isFollower`, and
  `hal.simulation.registerSimPeriodicBeforeCallback`.
- REV's simulated sensor noise in the C++ library comes from `std::random_device` and cannot be seeded from Python:
  identical inputs give velocities differing by about ±0.05% between processes.
- `robotpy-rev` ships a MAXSwerve example (`examples/maxswerve`), a RobotPy port of REV's template.

Decisions made with the user:

- Option A for repeatability: accept that RobotPy REV runs are close but not identical (velocity closed loops see the
  noise); document it. A deterministic `LD_PRELOAD` mode may come later if needed.
- Same scope as the Java support (duty cycle/voltage, closed loop, absolute encoder, limits, followers; the existing
  `maxswerve` part and `swerve-rev` template).
- Approach A: the helper lives in Scrimmage's RobotPy lockstep extension (`scrimmage-lockstep`), which RobotPy
  configs already load with `--scrimmage-lockstep`.

## Goals

1. A RobotPy robot using `robotpy-rev` SPARKs runs under Scrimmage with no robot-code changes, in lockstep, faster
   than real time, driven by REV's own simulated controller.
2. One protocol for Java and Python: Scrimmage's bindings and checks need no language-specific code beyond console
   parsing and wording.
3. Missing pieces (helper not loaded, old lockstep package, old robotpy-rev) are reported in plain words.

## Non-goals

- Exact repeatability for RobotPy REV runs (see Context).
- REV devices beyond SPARK MAX/Flex; the non-goals of sub-project 6 apply.
- RobotPy robots that don't load the Scrimmage lockstep extension.

## Design

### Robot side: `scrimmage_lockstep/rev.py`

- `scrimmage_lockstep.loadExtension()` calls `rev.install()` after loading the HAL extension. `install()` returns at once
  when `robotpy-rev` isn't importable.
- Otherwise it wraps `rev.SparkMax.__init__` and `rev.SparkFlex.__init__` in place (the class objects themselves, so
  `import rev` and `from rev import SparkMax` both see it): after the original constructor, the instance is
  registered. The first registration installs the per-loop helper with
  `hal.simulation.registerSimPeriodicBeforeCallback`.
- The per-loop helper is a Python port of `ScrimmageRev.java`'s loop, with the same protocol and rules: helper SimDevice
  per SPARK via `hal.SimDevice`; `iterate(v, max(vbus, 0.1), dt)` with `v` in the selected feedback sensor's converted
  units (motor RPM × encoder velocity factor, or absolute-encoder RPM × its factor when the closed loop uses
  `kAbsoluteEncoder`), negated for an inverted SPARK; the position preset so `iterate`'s integrated position lands on
  the real one; the absolute-encoder position per REVLib's zeroOffset definition; limit switches; followers copy their
  leader's physical output; `dt` from `wpilib.Timer.getFPGATimestamp()` differences, first loop 0.02, `dt <= 0`
  skips `iterate`; `motorVoltage = appliedOutput × vbus × (inverted ? -1 : 1)`; the SparkSim is rebuilt only when the
  chosen DCMotor changes; robot `setPosition` on the built-in encoder is preserved (the Java helper's reconciliation).
- `SparkMaxSim.iterate` / `SparkFlexSim.iterate` are replaced by a wrapper that ignores calls from outside the helper
  and counts them (a context flag set around the helper's own calls); once the helper is unavailable, robot calls pass
  through.
- Console lines (read by Scrimmage): `scrimmage rev helper: loaded (python <scrimmage-lockstep version>)` at install;
  `scrimmage rev helper: REV sim unavailable: <reason>` once if a needed API is missing; `Scrimmage rev helper:
  ignored N robot-side sim writes (...)` summaries like the Java agent's.
- No noise seeding (see Context).

### Scrimmage side

- `robot_process.py` parses the helper's lines into the same `rev_state` (and the agent-like state used by the REV
  device checks) as the Java agent's lines.
- The bridge's "isn't driving the robot's REV SPARKs" warning words its fix by language: for a RobotPy robot (the
  config's command runs `robotpy`), "add `--scrimmage-lockstep` to `[robot_program] command`, or update scrimmage-lockstep
  (`pip install ./lockstep`)".
- `scrimmage-lockstep` version bumps to 0.2.0; when a RobotPy config binds REV devices, Scrimmage checks the
  installed version (`importlib.metadata`) at session start and refuses a version below 0.2.0 with "reinstall it:
  `pip install ./lockstep`".
- `robotpy-rev` joins the `robotpy` and `dev` extras (pinned `>=2026.0.4,<2027`).

## Failure handling

| Situation | Behavior |
|---|---|
| RobotPy config binds REV devices; robot runs without `--scrimmage-lockstep` | warning: the Scrimmage REV helper isn't loaded; add `--scrimmage-lockstep` to `[robot_program] command` |
| `scrimmage-lockstep` older than 0.2.0 | session start error naming `pip install ./lockstep` |
| `robotpy-rev` lacks an API the helper uses | `REV sim unavailable: <reason>` → failed "REV simulation available" check, Drive-tab notice |
| Student code calls `iterate` | ignored, counted in the helper summary |
| Two runs differ slightly | expected; docs explain the unseedable noise |

## Testing

- Unit (plain Python, fake `rev` and `hal` modules): constructor wrapping (both import styles); the loop math
  (velocity conversion, absolute encoder with offset and inversion, followers, position preset and robot
  `setPosition`, `dt <= 0`, vbus floor, rebuild only on motor change); the `iterate` guard and summary line; console
  parsing; the version check; the RobotPy wording of the warning.
- End-to-end (real `robotpy-rev`; run by CI's test jobs, which install the dev extra and `./lockstep`):
  `tests/rev_probe_py/` (a small RobotPy robot mirroring the Java probe): duty cycle, position PID, absolute encoder,
  forward limit, inverted follower, disabled 0 V, lazily created SPARK, faster than 1x, student `iterate` ignored. No
  repeatability check (comment says why).
- Example: `examples/robotpy_maxswerve` = robotpy-rev's MAXSwerve example with its license notices;
  `configs/maxswerve_py.toml` (the `swerve-rev` parts with a RobotPy `[robot_program]`);
  `scenarios/maxswerve_py_auto.toml` with a pose check whose tolerance allows for the noise.

## Docs

- `docs/rev.md`: a "RobotPy" section — setup (`robotpy-rev`, `--scrimmage-lockstep`, reinstalling `./lockstep`), why runs
  aren't byte-identical, and the example.
- README: the REV section mentions RobotPy.
