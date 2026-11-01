# Point at the code and run: design

Sub-project 16. Branch `robot-code`, from `bench-tab`.

## Why

The owner wants as little left to the command line or to hand-edited text files as is reasonable. Today, getting a
team's robot code running against a robot model means hand-editing the config's `[robot_program]` (the command and the
folder), knowing about `scrimmage java build` and the slow first Gradle build, typing a start pose as numbers, and,
when a CAN ID is wrong, spotting a warning in the terminal. The scenario report doesn't even mention it (usability
item 2).

The team works in two roles, often on different laptops:
- a CAD student builds the robot model;
- a programmer later points it at the robot code and runs it.

The team shares files by flash drive and email today, and is willing to adopt a shared folder. Not everyone will learn
git, but the team may adopt it later.

This is the first of two projects agreed on 2026-10-31. The second, building the robot from CAD in the browser, comes
next. The owner's Inventor plugin, planned within two weeks, should write the same part-folder format the FreeCAD
exporter writes, so every path ends at this project's step.

## Who

- Programmers: students and mentors. This is the main path.
- CAD students, who hand over a model.
- Mentors who aren't programmers.
- Team 5098 uses Java with CTRE Phoenix 6 (Krakens, Tuner X swerve). RobotPy and REV must work too.

## Goals

1. **A Robot code panel in the Robot tab** (mockups 2026-10-31):
   1. **Your robot code.** Pick the robot project's folder (Browse). Scrimmage says in plain words what it found,
      e.g. "Java (GradleRIO) · CTRE Phoenix 6 · Tuner X swerve", or "Python (RobotPy) · REV". It writes
      `[robot_program]` (command and folder) itself.
   2. **Ready to start?** A checklist in plain words, each problem with a button that fixes it where the page can:
      - WPILib's Java found;
      - Scrimmage's Java helper built (a button runs `scrimmage java build`);
      - the project built once (a button runs the first Gradle build, with progress, so the first start doesn't time
        out);
      - the lockstep extension installed;
      - a note that CTRE robots run in real time;
      - for RobotPy, the RobotPy packages present.
   3. **Where it starts.** There is no default.
      - Until a spot is picked, the panel says "Not picked yet", and Drive and test runs ask for one before the first
        start.
      - Picking means choosing a field, then clicking to place the robot and dragging to turn it. The fields are
        Scrimmage's built-in ones (the current year first, older ones after) plus a plain field: a generic red/blue
        rectangle.
      - Once picked, the panel shows a plain sentence ("Starts on the blue side, facing the red wall") and the field
        drawing with the robot at its real size, with a triangle pointing the way it faces.
      - The exact numbers are under "Exact position".
      - The chosen field is also the field the robot drives on (the config's `[range]`, or none for the plain field).
   4. **Start and check devices**, and **Drive**.
2. **Device check: catch ID mistakes early.**
   - After a start (and as the robot keeps running, so devices made later, in teleop say, appear), a table lists every
     device the model expects beside what the code actually made.
   - Mismatches get a suggested fix: "fr · drive expects TalonFX 3, nothing with that ID; use TalonFX 13".
   - A device the code made that the model lacks is offered as "Add as…".
   - **Apply the matches and restart** writes the fixes into the robot file.
   - The same mismatches also appear in scenario reports and the Drive tab, not only in the terminal (usability
     item 2).
   - Readable in light and dark themes: icons and bold labels, never pale tinted rows (the owner found the mockup's
     red rows hard to read).
3. **Team folders: a CAD folder and a code folder per computer.**
   - Each computer knows where the team's CAD folder and code folder are. They are set once on the start page ("Where
     is your team's CAD on this computer?") and can be a shared drive, a flash drive, or later a git checkout, where
     both may point into one repo.
   - Robot files store paths relative to them: part folders as "in the CAD folder: arm", the robot code as "in the code
     folder: 5098-2026". This uses placeholders like the existing `{scrimmage_java}` and `{python}`, e.g. `{cad}/arm` and
     `{code}/5098-2026`.
   - The same robot file then works on every laptop.
   - A folder this computer hasn't set yet means the page asks for it once, in plain words, instead of failing.
   - Robot files usually live in the CAD folder (the CAD student makes them first); a programmer only points at the
     code.
   - Existing robot files with relative or absolute paths keep working.
4. **Motor settings the code can't show:** brake or coast, and invert, become proper controls on each motor in the
   part inspector instead of free-text binding keys, with the plain-words reason (Scrimmage can't read brake mode
   from the code).

## Non-goals

- Building the robot from CAD in the browser (the next project), and the Inventor plugin.
- Reading CAN IDs or gear ratios from TunerConstants.java. The device check matches IDs from what the code actually
  makes, for any language; gear ratios stay as they are.
- Fast mode setup (the IO-class restructuring stays a programmer's job, docs/fast-mode.md).
- Forms for `[[analog]]` / `[[digital]]` hand-written bindings, `[lockstep]`, `[telemetry]` and `[model]`. They stay
  hand-edited (rare; shown read-only as today). `[battery]` gets a small form under "More settings".
- Making git required.

## Anti-goals

None named. Builder's guard: never write a robot file that only works on this laptop when a team folder covers the
path; never start the robot from a spot nobody chose; never hide a device mismatch.

## Constraints

- **Build on the existing app:** the Robot tab (assembler), its document handling (edits keep comments and order,
  undo, stale-edit refusal), the field picker (`fieldpick.js`), the start page (Task 8 of sub-project 15), and the
  Drive tab's live session (it already knows the devices the code created, `bridge.hal.devices`, and warns about a
  configured device the code never made).
- **Readable in light and dark themes,** contrast at least WCAG AA, checked by a browser test (one already checks
  placeholder contrast).
- **Plain words** throughout, for high-school students and mentors who aren't programmers.
- **The CLI keeps working:** hand-written configs, `scrimmage run`, `serve`, and existing paths.
- Linux is tested; Windows and macOS stay "should work" (team folders must not assume Linux paths).
- Commits end with the Co-Authored-By line. Verify locally; PR order after 15.

## Decided with the owner

- Two projects: this one first, then CAD in the browser.
- The panel's three steps and the device check, as mocked up.
- No default start; a field picture with a triangle marker; a choice of field years plus a plain red/blue field.
- Readable contrast.
- Catching ID errors early.
- A CAD folder and a code folder per computer instead of requiring git.
- Robot files usually live in the CAD folder.

## My calls (the owner may override)

- **The chosen field is the `[range]`;** the plain field means no range.
- **Placeholder names:** `{cad}` and `{code}`. The per-computer settings live in a small user settings file next to
  the recent-files list.
- **Brake/coast and invert controls,** and a small `[battery]` form under "More settings".
- **"Apply the matches"** edits the part `ids` through the existing edit operations, so Undo covers it.

Settled after the builder check:

- **Which fields the picker offers.**
  - Only the current year's game field (`2026`, the only full field Scrimmage has) and the plain red/blue field. The
    other built-in ranges (2022 hangar, 2020 switch, 2013 pyramid, 2023 charge station, the bump and terrain
    libraries) are single elements on an empty floor, not fields, so they aren't offered as years.
  - A config that already names one shows it by name, read-only, with "Change" offering the two choices.
  - Choosing 2026 keeps its own floor-ball default. A short line says the 360 FUEL make runs slower, with a "no balls
    on the floor" switch (`floor_balls = false`).
  - When a range has named starts, the picker shows them as quick choices beside free placement. A free placement
    writes `[robot] start_pose` and removes `[range] start` (the loader refuses both together).
- **Which Python runs a RobotPy project.**
  - The project's own environment when it has one (a `.venv` in the project folder); otherwise the Python running
    Scrimmage, as `{python}` does today.
  - The checklist checks that RobotPy and the lockstep extension import in that Python.
  - The fix button installs the extension from Scrimmage's own folder (Scrimmage is installed editable from its
    checkout, so the folder is known), or says the command when it can't.
- **No default start.**
  - An existing `start_pose` or `[range] start` counts as picked.
  - New robots from the templates and the start page stop writing a `start_pose`.
  - Only starts from the page (Drive, Run on the Scenario tab, Start and check devices) ask for a spot.
  - The loader keeps its default for files with none, so `scrimmage run`, `serve`, the agent and existing scenarios
    behave as today ("the CLI keeps working").
- **`{cad}` and `{code}` resolve everywhere, not just in the page.**
  - The loaders (robot config, part folders, `[robot_program]` command and cwd, `[range]` file, a scenario's
    `config`) resolve them from the per-computer settings file.
  - Environment variables `SCRIMMAGE_CAD` / `SCRIMMAGE_CODE` override it (for CI or another machine).
  - When neither is set, the error says in plain words which folder to set and how.
- **How far the device check reaches.**
  - It covers what the bridge can match by ID: CTRE Phoenix 6 (TalonFX, CANcoder, Pigeon 2), REV SPARKs, and
    fast-mode devices.
  - It suggests a fix only within the same device type. When the code made a different type (a SparkMax where the
    model has a TalonFX), it says so in plain words and points to the part inspector; nothing is auto-applied.
  - PWM, DIO, analog and other SimDevice sensors are listed as "not checked by Scrimmage yet", not matched.
- **Windows and macOS.** The lockstep item shows "not available on this computer: the robot runs in real time" and
  never blocks starting.

## Left to the builder

- How project kind is detected (build.gradle plus vendordeps for Phoenix/REV; robot.py or pyproject for RobotPy).
- The checklist's exact checks and fix buttons.
- How the first Gradle build's progress is shown.
- The settings file format.
- How "Add as…" maps an unknown device to a part slot.
- The field drawing for older years.

## Decisions made during the build

Rulings taken while building (2026-10-31), each with what it costs if wrong.

- **Existing tests.** Tests that drive or run a template robot with no start now set a start; the page's "pick a start first" rule wasn't weakened. Cost if wrong: test churn only.
- **Range starts.** A config with a `[range] start` counts as picked. Cost if wrong: none.
- **`{cad}` and `{code}` placement.** "Robot config" here means placeholders inside a robot file and in a scenario's `config`. The CLI's own file argument doesn't expand them (a shell passes them literally). Cost if wrong: one extra expand in the CLI.
- **Session status keys.** `Session.status()` keeps `devices` and adds `device_check` and `device_notes`. Cost if wrong: none.
- **Never hide a mismatch.** Device notes cover the code making none of the expected devices, and devices still not made when a run ends. Robot time counts from connect. Cost if wrong: none.
- **Hand-written robot models skip the start rule.** The page can't edit their start. Cost if wrong: such robots start without being asked.
- **Path form.** Paths the page writes become `{cad}`/`{code}` only when the target is in a different team folder from the file; otherwise they stay relative. Both are portable. Cost if wrong: none.
- **The start picker.**
  - Cancel undoes only the picker's own edits, and the undo carries the version it checked, so it can never undo someone else's.
  - The robot is drawn dark slate, and the plain field's box is labeled "practice box".
  - A Scenario-tab Run with no start opens the picker.

  Cost if wrong: none.
- **A whole-app dark theme stays,** with a contrast test over every screen in both themes. The light theme is unchanged except the pressed Teleop/Test text, darkened from 4.44:1 / 4.32:1 to pass. Cost if wrong: a larger test.
- **Docs.** The `--new` templates point to the Robot code panel; usability item 9 is done for the page. Cost if wrong: none.
- **Final review fixes.** The final review's fixes went in one wave:
  - no stray "null";
  - page starts refuse unbuilt Java code, with Build now;
  - only real build outputs count as built;
  - restarts are reported truthfully;
  - one "Browse…" form;
  - a Stop button;
  - the guide covers team folders and "Not checked yet".

  Cost if wrong: none.
- **Parked:**
  - **A Scenario-tab Run from the page didn't check the build** (parked at the final review). Done afterwards with
    the owner's OK: `start/not_built` in `App.run`, with **Go to Robot code** on the Scenario tab (commit 7af0297).
  - **Accepted:** a failed build is remembered only while the editor runs. After a restart, an old good jar reads as built, but the start then fails fast with the console output.
