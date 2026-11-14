# Lockstep on Windows

Status: design. The owner asked for the spike, then said "Go ahead" to the full job (2026-11-13). Calls are mine
(decide-and-document); each **Call** says what it costs if it's wrong.

## Why

Lockstep lets scrimmage pause the robot program, run it faster than real time, and make runs repeat. It is Linux
only: on Windows, which most FRC students use, the robot runs on the wall clock. PR #44 made the RobotPy helpers
(REV, LaserCAN) install there, but not lockstep itself.

## What the spike found (2026-11-13, branch windows-lockstep-spike, Windows CI, Python 3.13, RobotPy 2026.2.2)

- robotpy-native-wpihal's win_amd64 wheel ships `wpiHal.dll`, `wpiHal.lib` and the headers. The extension builds with
  MSVC once it links `wpiHal.lib` and installs as RUNTIME; the loader picks `halsim_scrimmage_lockstep.dll`.
- tests/test_lockstep_ext.py: 7 of 7 on Windows (clock stepping, holding, watchdog, engage and release).
- tests/test_rev_py_e2e.py: 15 of 16, including "faster than real time" and "robot clock matches scrimmage time".
  The failure is `test_two_runs_give_identical_physics`: identical for 0.56 s, then apart once REV's C++ sensor noise
  starts (std::random_device; on Linux scrimmage pins it by LD_PRELOADing scrimmage_seed). Java REV noise is already
  neutralized by the agent (its NoiseGenerator patch), so it is RobotPy REV only.

## Goals

- RobotPy and Java robots run in lockstep on Windows (x64): pausable, faster than real time, the same scenario
  results as Linux, except RobotPy REV runs not being bit-identical (Call W3).
- A student installs it with no C++ compiler (Call W1).
- The robot program never outlives scrimmage on Windows, as on Linux (Call W4).
- Windows CI runs the lockstep tests on every PR.

## Non-goals

- macOS (no CI job, untested: stays real time, and the docs keep saying so).
- Windows on ARM (no wheel from RobotPy to link against; real time there).
- Repeatable REV noise for RobotPy on Windows (Call W3).

## Anti-goals

- Asking students to install Visual Studio Build Tools.
- A Windows path that silently runs in real time while scrimmage says lockstep.

## The design

### Call W1: a prebuilt DLL in the repository

On Windows the lockstep package stays pure Python (no CMake, no compiler, as PR #44 made it), and carries a prebuilt
`halsim_scrimmage_lockstep.dll` as package data: `lockstep/scrimmage_lockstep/lib/win_amd64/halsim_scrimmage_lockstep.dll`,
committed, with a stamp file beside it holding the SHA-256 of the C++ source and CMakeLists.txt it was built from.
The fast-mode library's committed Maven jars are the precedent.

- CI builds it on Windows from source on every run and tests against the fresh build (so the committed binary is
  never what the tests trust).
- A test (it runs on Linux too) checks the stamp matches the current source: changing the C++ without committing a
  new DLL fails CI, with a message saying how to regenerate it (download the Windows job's artifact; a short
  script, `lockstep/tools/update_windows_dll.py`, does it with `gh run download`).
- The DLL links `wpiHal.dll` by name and uses only the HAL's C API, so it works across RobotPy 2026.x, as the Linux
  build does against `libwpiHal.so`.
- *Cost if wrong:* a binary in the repo (about 35 KB) that a maintainer must refresh when the C++ changes; the stamp
  test makes forgetting loud. If a RobotPy update breaks the HAL ABI, the Windows DLL fails to load: the loader must
  report that in plain words and fall back to real time (W5).

### Call W2: the loader and the gates

- `library_path()` returns the platform's file (`.so` on Linux, the win_amd64 DLL on Windows x64).
- `robotcode._check_lockstep`, `_check_realtime`, README "Limits", docs/rev.md, docs/sensors.md and the Robot code
  panel's wording say Linux and Windows. macOS keeps the real-time wording.
- `seed_preload` stays Linux only.
- Java needs nothing new: the init script already appends `library_path()` with `File.pathSeparator`.

### Call W3: REV noise on Windows stays unpinned

RobotPy REV robots on Windows run in lockstep but their C++ sensor noise isn't pinned, so two runs drift apart once
noise matters (velocity control, in the spike after 0.56 s). Robots without REV, and Java REV robots, repeat.
`test_two_runs_give_identical_physics` is skipped on Windows with that reason; the docs say it where they promise
repeatable runs. Pinning it would mean patching the C runtime import in REV's simulation DLL at run time, which is
fragile across REV releases. *Cost:* a RobotPy REV team on Windows can't diff two runs bit for bit; their scenarios
still pass on tolerances.

### Call W4: robot process lifetime on Windows

- The robot program is put in a Windows job object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` (ctypes, no new
  dependency), held by scrimmage, so when scrimmage exits for any reason (including being killed) Windows kills the
  robot and its children. That is Linux's PR_SET_PDEATHSIG.
- `kill_by_token` finds stray robot processes by their environment on Linux (/proc). Windows can't read another
  process's environment, so the Gradle init script also puts the token on the robot JVM's command line
  (`-Dscrimmage.runToken=<token>`), and on Windows `kill_by_token` finds processes whose command line holds it (a
  CIM query through PowerShell, or WMIC where present), then `taskkill /F /T`. RobotPy robots are in the job.
- *Cost:* a robot JVM spawned by a Gradle daemon outside the job (only if the student's Gradle uses a daemon) is
  found by command line; if the query fails, it may linger until the student closes it, as today.

### W5: failure modes, in plain words

- The DLL is missing or doesn't load (a RobotPy version whose HAL it can't bind): the loader logs one line saying
  lockstep couldn't load and the robot runs in real time, with the reason; scrimmage's existing "lockstep extension
  not loaded; running in real time" covers the session.
- Windows on ARM or macOS: the existing real-time line.

### CI

- The `windows` job builds the extension from source with MSVC, uploads it as an artifact, runs
  tests/test_lockstep_ext.py and tests/test_rev_py_e2e.py (the identical-physics test skipped, W3) and the
  existing RobotPy distance e2e, against the fresh build.
- A Java lockstep check on Windows: the java job's lockstep test (tests/test_java_e2e.py's lockstep or speed test,
  whichever is smallest) on a Windows runner, so the init script's Windows path is exercised.
- The spike's workflow file is deleted.

## Tests

- The stamp test (all platforms).
- The loader picks the right file per platform (unit, with sys.platform faked), and the missing/unloadable DLL path
  logs and falls back (unit).
- Job object: a Windows-only test that a child of the robot program dies when the job is closed (Windows CI).
- kill_by_token on Windows: finds a process by a token on its command line (Windows CI).
- Everything in CI above.

## Left to the builder

- Where the job-object code lives (robot_process.py is the natural place) and how it's held for scrimmage's lifetime.
- Whether the Windows CI job's fresh build replaces the committed DLL in the installed package for the tests (it
  must: the tests test the source).
- The exact wording of the new log lines, following the existing ones.

## Answers to the builder check (2026-11-14; all mine)

1. **Java on Windows was never run.** The first task proves it on Windows CI (the JVM loads its JNI `wpiHal.dll`
   before HALSIM extensions, so the extension should bind to it, but that's unobserved). **Call W6:** if it doesn't
   work and the fix isn't small, Java stays real time on Windows (RobotPy only), the goals and docs say so, and the
   Java CI check becomes a "runs in real time, no wrong lockstep claim" check. *Cost:* Java teams on Windows wait.
2. **Install path.** scrimmage-lockstep is installed from the checkout (`pip install ./lockstep`; the panel's
   `install_lockstep` fix runs that). The Windows wheel stays pure Python and carries the DLL as package data (it
   ships on macOS too, unused). The loader uses it only when `sys.platform == "win32"` and `platform.machine()` is
   AMD64/x86_64; anywhere else, real time with the existing line. A DLL that fails to load: the W5 fallback.
3. **Version.** The package goes to 0.8.0. On Windows the Robot code panel and the session ask for 0.8.0 for lockstep
   ("update it to run faster than real time"); an older install still gets the helpers in real time, so a REV or
   LaserCAN robot keeps working. Linux's LOCKSTEP_VERSION requirement is unchanged.
4. **What exists.** `kill_tree` (taskkill /F /T) stays as `stop()`'s Windows path. The job object is added only for
   "scrimmage died"; `kill_by_token` gains the Windows path. `start_new_session` is ignored on Windows, as now.
5. **Jobs.** Assigned right after the process starts (asyncio can't create it suspended). Nested jobs are allowed
   since Windows 8; if assignment fails anyway, log one line and go on (stop() and kill_by_token still work). A child
   the robot spawns before assignment escapes the job: RobotPy runs in one process; for Gradle the JVM starts well
   after, and the command-line token covers it.
6. **Token.** A random run id, not a secret. Linux keeps /proc/environ. Windows queries CIM through PowerShell (on
   every Windows 10/11; no WMIC). If the query fails, warn once that a robot program may still be running and name
   the token, rather than staying silent.
7. **The stamp** says which source the committed DLL came from; it doesn't claim a reproducible binary (the
   compiler and HAL headers can differ). The DLL is only ever taken from this repo's own Windows CI run on the same
   commit (the update script refuses anything else). A PR that changes the C++ fails the stamp test until a
   maintainer regenerates it; C++ changes are rare, and the Windows job tests the fresh build regardless.
8. **Line endings:** the stamp hashes the files with CRLF normalized to LF (and a .gitattributes keeps the C++ LF).
9. **The skip** is REV-specific: `test_two_runs_give_identical_physics` is the REV probe's, skipped on Windows only.
   A Windows CI check also shows a RobotPy robot *without* REV repeats bit for bit (an existing repeatability test if
   one fits, else a small one).
