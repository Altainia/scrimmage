# Settings

The editor's top bar has a **Settings** button. It opens a small menu of choices that apply to every tab, and to
every editor and command on this computer. It has three settings: **Units**, **Language** and **Theme**.

Contents: [Units](#units), [What each quantity is shown in](#what-each-quantity-is-shown-in),
[Typing a value](#typing-a-value), [Your files don't change](#your-files-dont-change),
[What follows the setting](#what-follows-the-setting),
[What never changes: machine output](#what-never-changes-machine-output),
[The command line: --units](#the-command-line---units), [Language](#language), [Theme](#theme),
[Where the setting is kept](#where-the-setting-is-kept), [Known gaps](#known-gaps).

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

## Language

**Language: English (US) | English (UK).** English (US) is the default: it is the words the code is written in.
English (UK) is British English: "colour", "centre", "metre", "aluminium", "grey", "towards" and the other British
spellings. Each language comes from a file in `scrimmage/locales/`, so the menu lists every one Scrimmage has, by
its own name. To add one, see [translating.md](translating.md).

Language is separate from Units: picking English (UK) doesn't switch to metric, and picking Metric doesn't change
the spelling. FRC is in inches and pounds, and many teams outside the US still work in them.

The choice applies at once, like Units: every open tab shows its words again in the new language, with no reload,
and a box you are typing in keeps your text. Text that was already written keeps its language: a finished run's
report still reads as it was written until you run it again.

What follows the Language:

- every tab's words: buttons, labels, tooltips, the Test tab's blocks, the drawings, notices and errors;
- the server's words on the page: problems in a file, run outcomes and check lines, Bench headlines and Measure
  text;
- the files written for people from the web app or by an agent's MCP call: `report.md`, a bench run's
  `summary.txt` and its chart labels.

What never changes: the same machine output as for Units ([above](#what-never-changes-machine-output)). MCP
results, `report.json`, `summary.json`, and the JSON and CSV files stay American English whatever the setting, so
agents and scripts see no change. File keys and values never change either: a file still says `color = "red"` and
`center = [1, 2]`, even when the page around them says "colour" and "centre".

**The command line** is American English: what `scrimmage run`, `test`, `bench` and the other commands print, and
the report files they write. `scrimmage --language en-GB <command>` sets the language for that one run of the web
app (`edit` and `serve`) and the reports it writes; like `--units`, it goes before the command, and a pick in the
Settings menu wins after that:

```bash
scrimmage --language en-GB edit configs/kitbot.toml
```

## Theme

**Theme: System | Light | Dark.** System is the default: the page is light or dark as your computer is set, as it
always was. Light and Dark keep the page that way whatever the computer says. Every tab follows a change at once:
its colors, the 3D views, the Test tab's blocks, the charts and the field drawings.

Theme has no command-line flag: it only changes how the web app looks.

## Where the setting is kept

In a small file, `settings.toml`, in your user settings folder:

| System | Folder |
|---|---|
| Linux | `~/.config/scrimmage/` (or `$XDG_CONFIG_HOME/scrimmage/`) |
| macOS | `~/Library/Application Support/scrimmage/` |
| Windows | `%APPDATA%\scrimmage\` |

```toml
units = "metric"      # "imperial" (the default) or "metric"
language = "en-GB"    # "en-US" (the default), "en-GB", or another language file's tag
theme = "dark"        # "system" (the default), "light" or "dark"
```

Set `SCRIMMAGE_CONFIG` to a folder to keep it there instead. With no file, the defaults (Imperial, English (US) and
System) apply. A file Scrimmage can't read, or a value it doesn't know, is reported once in the editor and on the
command line, and the default is used for that setting; the others keep their values.

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

For Language:

- **The Test tab's sweep and floor spread tables** keep their outcomes and grip-loss words in American English
  inside the translated table, because they come from the run's `table.csv`, which is machine text.
- **The command line is American English throughout**, whatever the setting: what it prints and the files it
  writes. Only the web app (`edit`, `serve`) and its files follow the Language.
- **A scenario run started from the web app** shows session and config errors (a robot config that can't load, for
  example) in American English on its run panel, because they are written into its `report.json`.
- **The Part tab's build messages** keep the language they were made in until the page is refreshed.
- **A few warnings from a running simulation** stay in American English: a sensor the robot blocks, a valve on a hub
  the robot code never opened, a missing device. They are made while the robot runs, and go into machine text too.
- **MCP results stay American English** (machine output), even when the web app shows the same run in British
  English.
- **The settings file's own problems** (a `settings.toml` that can't be read, or a value Scrimmage doesn't know,
  such as "theme = … isn't system, light or dark; using the System theme") are reported in American English: they
  are found before the language is known.

For Theme:

- **Older browsers** (before Chrome 123, Safari 17.5 or Firefox 120) show the light theme only, whatever you pick.
