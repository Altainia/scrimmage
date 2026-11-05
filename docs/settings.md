# Settings

The editor's top bar has a **Settings** button. It opens a small menu of choices that apply to every tab, and to
every editor and command on this computer. Today it has one setting, **Units**.

Contents: [Units](#units), [What each quantity is shown in](#what-each-quantity-is-shown-in),
[Typing a value](#typing-a-value), [Your files don't change](#your-files-dont-change),
[What follows the setting](#what-follows-the-setting),
[What never changes: machine output](#what-never-changes-machine-output),
[The command line: --units](#the-command-line---units), [Where the setting is kept](#where-the-setting-is-kept),
[Known gaps](#known-gaps).

## Units

**Units: Imperial | Metric.** Imperial is the default, because FRC is: field drawings, game manuals, stock
(1x2 tube, 4 in wheels) and 60 psi working pressure are all in inches, feet and pounds. Pick Metric if your team
thinks in millimeters and kilograms.

The choice applies at once. Every open tab shows its numbers again in the new units, with no reload. A box you are
typing in keeps your text, which then means the new units. Another browser tab or another editor on the same
computer picks the change up within about 2 seconds.

Text that was already written keeps the units it was written in. A test run or a scenario run that finished before
you changed the setting still says "0.5 in" until you run it again.

## What each quantity is shown in

| Quantity | Imperial | Metric |
|---|---|---|
| Small length (parts, mechanisms, offsets, sensors, clearances) | in | mm |
| Field length (poses, the start pose, field picks, drive distances, where a ball lands) | ft | m |
| Speed | ft/s | m/s |
| Mass | lb | kg |
| Force | lbf | N |
| Torque | in·lbf | N·m |
| Pressure | psi | kPa |
| Density | lb/in³ | g/cm³ |
| Spring rate | lbf/in | N/m |

Angles are degrees, time is seconds, current is amps (A) and voltage is volts (V) in both. Angular speed stays in
°/s or rpm, as it was. A few rare units have no Imperial form and are shown as they always were: damping (N·s/m,
N·m·s/rad), a hinge spring (N·m/rad) and a moment of inertia (kg·m²).

So a 4 in wheel reads **4 in** or **101.6 mm**, a 1/4 in gap **0.25 in** or **6.4 mm**, a robot 12.5 ft from the
blue wall **12.5 ft** or **3.81 m**, and working pressure **60 psi** or **413.7 kPa**.

The field drawing (the field picker, the start picker and the Drive tab's small field) draws its grid every 5 ft in
Imperial, with the numbers along the bottom and left in feet, and every 1 m in Metric.

## Typing a value

A box takes a number in the unit next to it. You can also type the unit:

- **A bare number** is in the unit shown: `4` in an inches box is 4 in.
- **A number and a unit** works too: `4 in`, `4"`, `100mm`, `12.5 ft`, `10 fps`, `2 Nm`, `0.0975 lb/in^3`.
  Spaces and capital letters don't matter.
- **Any length unit of your system** works in any length box. In Imperial a box in feet takes `6 in`, and a box in
  inches takes `2 ft`. In Metric a box in meters takes `50 cm` or `500 mm`.
- **The other system's units are refused**, with a plain message that says what to type:
  "Units is set to Imperial, so type ft here, not m (or change Units in Settings)". Change the setting first if you
  want to type metric.

A value you type and save reads back the way you typed it: `6.55` ft stays 6.55 ft when you open it again.

## Your files don't change

Changing the setting never rewrites a file. A robot config, a part, a scenario, a test, a bench file or a
measurement file you don't edit stays byte for byte the same, whichever units you pick.

When you do edit a value, it is saved in the file's own units:

- **Robot, part, scenario and range files** keep meters, kilograms and degrees, as they always have. Type `4 in`
  and the file gets `0.1016`.
- **A key with a unit in its name keeps that unit.** A bench file's `diameter_in`, a test's `at_least_in` or
  `ahead_in`, and a measurement file's `<name>_<unit>` keys (`mass_g`, `force_lb`) are written in inches, grams or
  pounds whatever the setting. Type `101.6 mm` into `ahead_in` and the file gets `4.0`.
- **A block's `unit`** ("deg", "in" or "m") stays what the test file says. Its drop-down shows your system's name
  (in or mm), and the number you type is converted into the block's own unit.
- **New keys** are written the way that kind of file always writes them (meters in a bench file, for example).

## What follows the setting

Everything written for people to read, at the moment it is written:

- every number the editor shows, in every tab: the Robot and Part inspectors, the status bar's mass and center of
  gravity, the Drive tab's readouts and push force, the Scenario tab's checks and report, the Test tab's blocks,
  Setup, chart titles and failure text, the Bench tab's boxes and stage table, and the Measure tab's new columns;
- `report.md` beside a scenario run, a bench run's `summary.txt`, its charts and the scale bar on its pictures, and a
  bench folder's gallery page;
- the text the command line prints: `scrimmage run`'s check lines, `scrimmage test run`, `scrimmage bench run`,
  `scrimmage measure` and `scrimmage freecad export` (its mass line).

## What never changes: machine output

Scripts and agents read some output by machine. That output is the same whatever the setting, exactly as it was
before the setting existed, so nothing that reads it breaks:

- **JSON and CSV numbers stay SI** (meters, kilograms, newtons, m/s), or in the unit their name says: `min_in`
  is inches, a test's `signals.csv` keeps inches, psi and degrees, and a bench `results.csv` has `speed_m_s`.
- **A scenario's `report.json`** and the MCP `run_scenario` result keep their check messages in meters ("pose:
  0.40 m from target").
- **The Test tab's machine text** keeps its old words: the MCP `test_run`, `test_sweep` and `test_spread` results
  (their outcomes, check lines and refusals) and a sweep's or floor spread's `table.csv`. They are in inches and psi
  as they always were, with one decimal where they had one ("120.0 psi", "0.0 in"), except for three things that
  were always in meters:
  - a drive's distance ("didn't reach 4 m in 0.5 s (went 0.43 m)");
  - a move or joint check whose block `unit` is "m" ("stopped at 0 m short of 0.5 m");
  - Setup's partner overlap ("the partner robot (Setup) starts inside the robot (their outlines overlap by 0.75 m)").
- **The MCP bench headlines** (`bench_run`, `bench_compare`) stay in m/s, newtons and meters. The files those tools
  write for people (`summary.txt`, the pictures) follow the setting, so the Bench tab shows them in your units.
- **A message about a file's own numbers** names them in the file's units: a robot config's `[partner]` that starts
  inside the robot says by how much in meters, as its `start` is written.

## The command line: --units

`scrimmage --units imperial <command>` or `scrimmage --units metric <command>` uses those units for that one run,
without changing the setting:

```bash
scrimmage --units metric measure examples/measure/fuel.measure.toml
scrimmage --units metric run scenarios/kitbot_auto.toml
scrimmage --units imperial edit configs/swerve.toml
```

`--units` goes before the command. Without it, a command uses the setting. If you start the editor with `--units`
and then pick Units in its Settings menu, your pick wins for the rest of that run (and is saved as the setting).

## Where the setting is kept

In a small file, `settings.toml`, in your user settings folder:

| System | Folder |
|---|---|
| Linux | `~/.config/scrimmage/` (or `$XDG_CONFIG_HOME/scrimmage/`) |
| macOS | `~/Library/Application Support/scrimmage/` |
| Windows | `%APPDATA%\scrimmage\` |

```toml
units = "metric"      # "imperial" (the default) or "metric"
```

Set `SCRIMMAGE_CONFIG` to a folder to keep it there instead. With no file, the default (Imperial) applies. A file
Scrimmage can't read, or a value other than imperial or metric, is reported once in the editor and on the command
line, and the default is used.

## Known gaps

- **The Test tab's sweep and floor spread tables** show inches whatever the setting. They are read from the run's
  `table.csv`, which keeps its old words (above). `scrimmage test spread` prints the same table.
- **A run kept inside a sweep or a floor spread**, opened from its row, shows those same old words (its outcome and
  checks, such as "didn't reach 4 m"), whatever the setting: they are written once, during the run, for the table.
- **A run an agent made with MCP `test_run`**, then opened in the Test tab, shows its check lines in the fixed agent
  text (inches and psi, with a drive's distance in meters), whatever the setting: the run is saved inside machine
  text.
- **The launcher model's own output** (`scrimmage launcher run`: its table and its downrange and lateral charts) and
  the tables `scrimmage bench sweep` and `scrimmage bench compare` print keep their fixed units (m, m/s, N).
- **A bench sweep's From and To boxes** follow the setting, but the sweep chart's x axis and the stage table's
  swept-key column show the number as the bench file writes it (meters, or inches for an `_in` key).
- **Changing Units clears the Test tab's undo history**, because the steps hold numbers in the old units.
- **Another open tab or editor** follows a change within about 2 seconds, not at once.
