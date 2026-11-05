# Display Units Design (sub-project 20a)

**Status:** the owner asked for this on 2026-11-04 ("imperial/metric") and delegated design questions
(decide-and-document, 2026-10-28). Written 2026-11-04. Language localization (20b) is a separate, later setting.

## Why

FRC is imperial: field drawings, game manuals, stock (1x2 tube, 4 in wheels, 60 psi working pressure) and most US
mentors think in inches, feet and pounds. Teams in metric countries may want metric, and some of them still pick
imperial because FRC is imperial.

Today the web app mixes units. The Assembler, Part, Drive and Scenario tabs show meters and kilograms. The Test
tab shows inches and the pressure block psi. The Bench shows whatever key a file used, and the Part tab's spool has
its own in/mm toggle. A student moving between tabs reads 0.1016 m in one place and 4 in in another for the same
wheel.

## The survey (2026-11-04)

- **No shared helper.** There is no unit helper. `fmt()` (static/app.js) trims digits, and `UNIT` prettifies
  `kg*m^2`. Units are hard-coded at about 150–200 sites, most of them in:
  - static/test.js and test_run.js, which already convert to inches;
  - part_inspector.js and part.js;
  - bench.js and blocks_defs.js;
  - step_editor.js, fieldpick.js, code.js, readouts.js, inspector.js and app.js.
- **Server-built text:** blocks/run.py (most of the failure text, in inches and psi), blocks/placed.py, checks.py,
  scenario_runner.py, bench/report.py (headlines) and the bench charts.
- **Files already choose units by key name:** `<k>_in`, the measure files' `<name>_<unit>`, `_deg`, and the block
  JSON's `unit`.
- **No user settings store exists.**

## Goals

1. **One setting:** **Units: Imperial | Metric**, shown in a new Settings menu in the web app's header, which 20b
   will extend with Language and Theme.
   - **Where it's kept:** in a small user settings file, `<user config dir>/scrimmage/settings.toml`
     (`platformdirs` style; `$SCRIMMAGE_CONFIG` overrides it), so every tab, every browser on the machine and the
     CLI agree.
   - **The default is Imperial,** because FRC is.
2. **Every value the web app shows or lets you type is in the chosen system.** The system by quantity:

   | Quantity | Imperial | Metric |
   |---|---|---|
   | Small length (parts, mechanisms, offsets, sensors, clearances) | in | mm |
   | Field length (poses, start pose, field picks, drive distances, report positions) | ft | m |
   | Speed | ft/s | m/s |
   | Mass | lb | kg |
   | Force | lbf | N |
   | Torque | in·lbf | N·m |
   | Pressure | psi | kPa |
   | Density | lb/in³ | g/cm³ |
   | Spring rate | lbf/in | N/m |

   Angles stay in degrees, time in seconds, and current and voltage in A and V. Angular speed stays in the units
   it is shown in (°/s, rpm), in both systems.
3. **Typing** in a field takes the chosen unit. The page converts, and the file stores what it stores today.
   - **Robot, part, scenario and range files** keep meters, kg and degrees.
   - **A key with a unit in its name keeps it.** That covers `_in` keys and measure suffixes: the page converts to
     that unit when it writes, so files don't churn when someone switches the setting.
   - **New keys** are written the way that file type writes them today.
4. **Server-built text uses the setting:** check messages, failure messages, headlines, report Markdown and bench
   chart axes. One Python formatter (`scrimmage/units.py`) and one JS formatter (`static/units.js`) share a single
   table of units, factors and decimals, sent to the page so the two can't drift apart.
5. **Machine-readable output stays SI:** MCP tool results, `report.json`, CSVs and files on disk. The agent docs say
   so.
6. **CLI:** `scrimmage --units imperial|metric <command>` overrides the file for one run. Without it the CLI uses the
   settings file.

## Non-goals

- **Changing file formats,** or rewriting existing files' keys.
- **Language** (20b), and the theme toggle and radii (with 20b).
- **Mixed units within a system** (feet-and-inches like 3 ft 4 in): imperial field lengths are decimal feet.

## Constraints

- **Wording and tests:** American spellings and plain words. Tests use `.venv/bin/python -m pytest`, never `uv run`
  in the repo root.
- **Round trips:** a value typed and saved, then shown again, reads back the same in the chosen units (within the
  shown decimals). A file not edited is byte-identical whatever the setting.
- **Existing tests:** tests that assert displayed text set the units explicitly (a fixture setting
  `$SCRIMMAGE_CONFIG`), so they don't depend on the default.

## Left to the builder

- **Decimals per quantity and unit,** in the shared table. Students should read 4 in, 0.25 in, 12.5 ft, 101.6 mm,
  60 psi and 414 kPa.
- **The Settings menu's look,** within the header's style.
- **The order the tabs are converted in**, as long as each task leaves the app working.

## Decisions

Each decision gives the choice, the reason, and what it costs if it is wrong.

- **U1. Each site names its quantity kind.**
  - **The kinds:** each display or input site names a kind from the shared table: `length`, `field`, `speed`,
    `mass`, `force`, `torque`, `pressure`, `density`, `spring`, `angle`, `time`.
  - **Small length or field length:** a pose, start pose, field pick, drive distance, scenario `x`/`y`/`tol_xy`, or
    report rest point is `field`. Everything on a robot or mechanism is `length`.
  - **Cost:** a site that reads oddly (a 30 ft sensor range shown in inches) is a one-word fix.
- **U2. A change applies at once.**
  - **On the server:** it re-reads the settings file when the file changes on disk.
  - **On the page:** it re-renders what's open, with no reload. Text from a run that already finished, such as a
    failure message, keeps the units it was written in.
  - **Cost:** an old run's text can show the other system until it runs again.
- **U3. A Bench key with an `_in` twin:**
  - **Typing:** the page writes the form the file already has, converted to that form's unit. A key with no form
    yet is written in meters.
  - **Unchanged:** the twin rule itself (setting one drops the other) stays as it is.
- **U4. Block JSON's `unit` ("deg", "in", "m"):** its values are file formats and stay. The block's drop-down shows
  the chosen system's names: in or mm for a length, ft or m for a field length.
- **U5. Written reports:** `report.md`, bench `summary.txt` and the charts use the setting at the time they're
  written. JSON and CSV stay SI.

## Rulings during the build

Each ruling is recorded in the build ledger with its reason and cost; the user-facing result is
[docs/settings.md](../../settings.md).

- **Tests:** a conftest fixture points `$SCRIMMAGE_CONFIG` at an empty folder for every test (the product default,
  Imperial, applies), and `units("metric")` sets a system per test. The new tests are `tests/test_display_units*.py`
  (`tests/test_units.py` was already the motor units' file).
- **The settings file:** a missing file is the normal first run and warns nothing; an unreadable file, bad TOML or a
  bad value warns once each. The menu's choice replaces a `--units` override for the rest of that `scrimmage edit`
  run.
- **Reaching other pages:** a 2 s poll of `GET /api/settings`; the page that makes the change applies it at once.
- **Typing:** a length or field box takes any length unit of the chosen system (`6 in` in a feet box, `50 cm` in a
  meters box), and refuses the other system's with a plain message. Converted boxes show the parse messages, which
  name the unit to type, instead of "Enter a number."
- **U4, a block's `unit`:** an untouched block writes its own number and unit back (byte-identical); a typed value is
  converted into the block's existing length unit; a new move gets the system's ("in" or "m").
- **Test-tab signals** keep their fixed wire units (in, psi, deg, N), so `signals.csv` and MCP results don't change;
  the page converts them for its chart.
- **Machine text reproduces each area's text from before the setting, byte for byte.** That holds for the Test tab's
  MCP `test_run` words and a sweep's or spread's `table.csv` (inches and psi, with a drive's distance, a "m" joint and
  the partner's overlap in meters), written inside `units.machine_text()`. It also holds for scenario `report.json`
  and the MCP `run_scenario` check messages (meters), and for the MCP bench headlines (m/s, N, m). A robot config's
  `[partner]` refusal is in meters too. Task 5 checked the Test tab against main on every example test plus eleven
  cases that reach each unit-bearing line, and `tests/test_units_machine_text.py` pins main's text.
- **Human files follow the setting when written:** MCP `bench_run` and `bench_compare` write `summary.txt` and their
  pictures in the room's setting; only the text returned to the agent is fixed.
- **The Measure tab's new-column defaults:** lb or kg for mass, lbf (`lb`) or N for force.
- **The field drawing's grid:** every 5 ft, labeled in feet, in Imperial; every 1 m in Metric.
- **A units change clears the Test tab's undo history** (its steps hold numbers in the old units).
- **Known gaps, for a later cleanup:** the Test tab's sweep and spread tables show inches whatever the setting (they
  come from the fixed `table.csv`); the launcher model's own charts and the CLI's printed sweep and compare tables
  keep their fixed units; a bench sweep's chart axis and the stage's swept-key column show the file's form.
