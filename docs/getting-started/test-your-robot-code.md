# Test your robot code

For programmers who want a quick yes or no after every code change: "the auto still ends where it should", "the
elevator still gets there in time", "nothing hits the wall". Scrimmage plays a short script (a **scenario**)
against your real robot code with no window and checks the results.

[← All guides](README.md) · Needs: your robot running in the sim, from
[Run your robot code](run-your-robot-code.md) (a `sim/robot.toml` in your robot project)

## 1. Make a scenario

In your robot project's folder (venv active):

```bash
mkdir sim/scenarios
scrimmage edit sim/scenarios/auto.toml --new-scenario --robot sim/robot.toml
```

Keeping scenarios in their own folder makes them easy to run all at once (step 5). This writes a starter scenario and opens it in the browser's **Scenario** tab. You can edit it there with forms, or
close the page (Ctrl+C in the terminal) and edit the file in a text editor. The file is short. A scenario is a list of
**steps** (a mode, such as disabled or auto, held for some seconds), and each step can have **checks**:

```toml
config = "../robot.toml"         # the robot config, relative to this file
description = "our auto drives 1 m forward and turns to face 90 degrees"

[[step]]
name = "idle"
mode = "disabled"
duration = 1.0

[[step]]
name = "auto"
mode = "auto"
duration = 5.0
  [[step.check]]                  # where the robot really is at the end of the step
  pose = { x = 3.0, y = 4.035, heading = 90, tol_xy = 0.2, tol_heading = 6 }
  [[step.check]]                  # the battery never sagged below 7 V during the step
  always = { signal = "battery", min = 7.0 }
  [[step.screenshot]]             # a picture from above, with the path drawn on it
  view = "top"
  at = "end"
```

Positions in the file are in meters on the field (the Scenario tab shows them in your Units setting). The robot starts where you picked in the Robot tab's **Robot code** panel
(**Where it starts**; a new robot file has no start until you pick one, and the Scenario tab's **Run** asks first). A **starter scenario has no checks, so it always passes** (its report says so): add at least
one.

## 2. Run it

```bash
scrimmage run sim/scenarios/auto.toml
```

```
PASS  auto.toml
  ok   [auto] pose within 0.065 ft and 0.1° of target (x=9.779 ft, y=13.251 ft, heading=89.9°)
  ok   [auto] battery stayed >= 7
report: runs/auto-20261031-114956/report.md
```

When a check fails, it says what it measured. If the target had been `x = 4.0`, a meter further than the robot
drove:

```
FAIL  auto.toml
  FAIL [auto] pose: 3.345 ft from target, heading off by 0.1° (x=9.779 ft, y=13.251 ft, heading=89.9°); allowed 0.656 ft, 6°
```

The printed lines and `report.md` are in the **Units** setting (feet unless you pick Metric: see
[settings](../settings.md)); the scenario file and `report.json` stay in meters.

If your code made different devices than the robot file expects (a CAN ID that doesn't match, say), `report.md`,
`report.json` and the printed summary say so in a note, for example that the robot file's TalonFX 21 wasn't made and
your code made TalonFX 1. The Robot tab's device table shows the same and can fix it.

The report folder has `report.md` (the checks and the pictures), `robot.log` (everything your code printed) and
`run.csv` (every motor and joint, every few milliseconds). The `runs/` folder keeps itself out of git (Scrimmage
puts a `.gitignore` in it when it makes it), so you don't need to add it to yours.

The command's exit code is 0 when everything passed, 1 when a check failed, and 2 when it couldn't run at all
(a typo in the file, or the robot code crashed at startup).

## 3. Checks worth having

| Check | Use it for |
|---|---|
| `pose = { x, y, heading, tol_xy, tol_heading }` | The auto ends where it should. |
| `console = { contains = "auto: done" }` | Your code printed something (a line you add to your auto, say). |
| `always = { signal = "battery", min = 7.0 }` | Nothing draws so much current that the robot would brown out. |
| `never_touch = "obstacle"` | The robot never hit a named thing. |
| `at_end = { signal = "joint.elevator.carriage.position", approx = 0.75, tol = 0.01 }` | A mechanism got where it was told (meters or radians). |

The names for `at_end` and `always` come from your robot config. In the Scenario tab, the check form lists your
robot's real signal names, so you don't have to guess them. The full list of checks and signals is in the README's
[Scenario files](../../README.md#scenario-files).

## 4. Record a drive instead of writing it

To test teleop: open the robot's **Drive** tab (`scrimmage edit sim/robot.toml`), press **Record**, drive, stop,
and click **Save as scenario…** with a name such as `sim/scenarios/my_drive.toml`. The new scenario replays
exactly what you did, and you add checks to it like any other. See [Recording and saving](../drive.md#recording-and-saving).

## 5. Run them all

Give `scrimmage run` every scenario in `sim/scenarios/`:

```bash
scrimmage run sim/scenarios/*.toml
```

It runs them one after another (only one simulation can run on a computer at a time), prints each one's results as
it goes, and ends with one line per file:

```
2 of 3 scenarios passed
  PASS   sim/scenarios/auto.toml
  FAIL   sim/scenarios/my_drive.toml
  PASS   sim/scenarios/shoot.toml
```

The exit code is the worst of them: 0 when all passed, 1 when one failed, 2 when one couldn't run. Don't start two
`scrimmage run`s at once.

CTRE robots run in real time, so a 15 s auto takes 15 s to check. For faster checks that repeat exactly (for example
in GitHub Actions), see [fast mode](../fast-mode.md).

## 6. Run them on every push (GitHub Actions)

[`examples/ci/scrimmage.yml`](../../examples/ci/scrimmage.yml) is a workflow for your robot repository: copy it to
`.github/workflows/scrimmage.yml`. On every push and pull request it checks Scrimmage out beside your code, installs
it, runs `scrimmage run sim/scenarios/*.toml`, and keeps the `runs/` reports as the run's artifact. A failed check
fails the job. For a RobotPy robot, delete the two steps the file marks "Java robots only"; a RobotPy robot that
needs more packages than RobotPy itself also needs a `pip install -r requirements.txt` step. Add `ref:` to the
Scrimmage checkout to pin a version, so an update can't change your results unnoticed.

Scrimmage's own tests check that the file is a valid workflow and that every `scrimmage` command in it exists with
those arguments. Nobody has run it on a team's repository yet: if it breaks there, the job's log says where.

## If something goes wrong

- **Exit code 2 with a message about the file**: the message names the bad line and lists the valid choices.
- **A check fails and you don't see why**: look at the notes and the picture in `report.md`. A CAN ID that doesn't
  match your code is a note there, and a `WARNING` line in the terminal.
- **The same scenario gives slightly different results each run**: normal for CTRE robots (real time). Give
  tolerances some room, or use [fast mode](../fast-mode.md). REV and Python robots repeat exactly.

## Tests for Scrimmage itself

Working on Scrimmage's own code rather than your robot's? Install with `pip install -e ".[dev,nt,mcp]"` and run
`pytest` in the `scrimmage` folder. The README's [Tests](../../README.md#tests) section has the details.
