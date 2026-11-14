# Windows Lockstep Implementation Plan

**Goal:** RobotPy (and, if Task 1's CI check passes, Java) robots run in lockstep on Windows x64 with no compiler,
from a prebuilt DLL committed to the repo.

**Spec:** docs/superpowers/specs/2026-11-14-windows-lockstep-design.md (Calls W1-W6 and the builder-check answers).

**Branch:** windows-lockstep (worktree ../scrimmage-wls), on top of the spike commits (their CMake Windows branch and
loader name are the starting point; the spike's pyproject changes are reverted by Task A).

## Global constraints
- Tests: `PYTHONPATH=<worktree> /home/altainia/code/scrimmage/.venv/bin/python -m pytest`, never `uv run`.
- Linux behavior unchanged. Timing never gates a test. User-facing strings through i18n.t (scrimmage/), plain words.
- Nothing raises into the robot program from the lockstep package; problems are one log line.
- No new dependencies.

## Task A (implementer, local): everything verifiable on Linux
1. **Packaging (W1, answer 2):** lockstep/pyproject.toml back to main's Windows handling (the robotpy-native-wpihal
   requirement Linux-only; the `^(?!linux)` override: pure Python off Linux). The CMakeLists WIN32 branch stays (CI
   builds the DLL by running CMake directly). The prebuilt DLL lives at
   `lockstep/scrimmage_lockstep/lib/win_amd64/halsim_scrimmage_lockstep.dll` with `SOURCE.sha256` beside it; check
   (with `SKBUILD_WHEEL_CMAKE=false` as #44 did) that the pure wheel carries both files. Version 0.8.0.
2. **Loader (W2, W5):** `library_path()`: Linux `.so`; Windows on AMD64/x86_64 the win_amd64 DLL; else a path that
   doesn't exist (real time, the existing line). If loading returns non-zero on Windows, log one line (lockstep
   couldn't load, the reason/code, the robot runs in real time) and still install the helpers. Unit tests with
   sys.platform / platform.machine faked.
3. **The stamp (W1, answers 7, 8):** `lockstep/tools/stamp.py` (importable and a CLI) computes SHA-256 over
   src/scrimmage_lockstep.cpp and CMakeLists.txt with CRLF normalized to LF; `tests/test_lockstep_windows_dll.py`
   fails when SOURCE.sha256 doesn't match, with the regenerate instructions. `lockstep/tools/update_windows_dll.py`:
   `gh run download` the `lockstep-win-dll` artifact from an Altainia/scrimmage run whose head SHA is the current
   commit (refuse otherwise), check its stamp equals the source's, copy both files. `.gitattributes`: the C++ and
   CMakeLists LF; `*.dll binary`. A placeholder isn't allowed: until the controller commits the CI-built DLL, the
   stamp test is expected to fail (say so in your report).
4. **Version gates (W2, answer 3):** session/robotcode: on Windows, RobotPy and Java lockstep need scrimmage-lockstep
   >= 0.8.0 (constant beside LOCKSTEP_VERSION); older still gets the helpers in real time with "update it to run
   faster than real time". `_check_lockstep`/`_check_realtime` treat Windows x64 like Linux except there's no
   compiled .so check (check the DLL file instead); macOS and other machines keep the real-time wording. Phoenix
   robots still say real time. Tests per branch.
5. **Process lifetime (W4, answers 4-6):** in scrimmage/robot_process.py: a Windows job object
   (KILL_ON_JOB_CLOSE, ctypes, kernel32 CreateJobObjectW/SetInformationJobObject/AssignProcessToJobObject/
   OpenProcess) created once per scrimmage process and held for its life; each robot process assigned right after
   it starts; an assignment failure logs one line and continues. `kill_by_token` on Windows: PowerShell
   `Get-CimInstance Win32_Process` filtered by CommandLine containing the token, then taskkill /F /T; a failed query
   warns once naming the token. The Gradle init script adds `-Dscrimmage.runToken=<SCRIMMAGE_RUN_TOKEN>` to the
   robot JVM's jvmArgs (all platforms; harmless on Linux). Unit tests with the Windows calls faked; the real ones
   run in Task B's CI.
6. **Tests that need Windows (written now, run in CI):**
   - tests/test_rev_py_e2e.py: `test_two_runs_give_identical_physics` skipped on win32 with the W3 reason.
   - a Windows-only test that a child of a job-assigned process dies when the job handle closes, and one that
     kill_by_token finds a process by a token on its command line (`pytest.mark.skipif(sys.platform != "win32")`).
   - a non-REV RobotPy repeatability check (answer 9): find an existing two-run identical test for a RobotPy robot
     without REV (grep "identical"); if none, add a small one.
7. **CI (.github/workflows/ci.yml, windows job):** install as now; then build the DLL from source
   (`cmake -S lockstep -B build-lockstep` with the venv's Python, `cmake --build build-lockstep --config Release`),
   write its stamp with lockstep/tools/stamp.py, upload both as artifact `lockstep-win-dll`, and copy the fresh DLL
   over the installed package's `lib/win_amd64/` before the tests; run tests/test_lockstep_ext.py,
   tests/test_rev_py_e2e.py, the non-REV repeatability test, the new Windows-only tests, and the existing
   test_lockstep_grapple.py / test_distance_py_e2e.py. A new `windows-java` job (copy the java job's JDK/Gradle setup
   to windows-latest) runs the smallest Java lockstep test from tests/test_java_e2e.py (find one that asserts
   lockstep or faster than real time). Delete .github/workflows/windows-lockstep-spike.yml.
8. **Docs:** README "Limits" and quick start, docs/rev.md, docs/sensors.md, docs/fast-mode.md if it says Linux-only:
   Linux and Windows x64 run in lockstep; macOS and Windows on ARM real time; RobotPy REV on Windows isn't bit-identical
   (W3); Java on Windows per Task B's result (write it as supported; the controller edits if W6 triggers). A
   CONTRIBUTING/README development note on regenerating the DLL.

## Task B (controller): Windows CI rounds
Push, read the windows and windows-java jobs, fix until green; apply W6 if Java can't be made to work; download the
`lockstep-win-dll` artifact with update_windows_dll.py, commit it, push, and confirm the stamp test and every job pass.

## Task C: final review (opus), one fix round, PR, merge on green.
