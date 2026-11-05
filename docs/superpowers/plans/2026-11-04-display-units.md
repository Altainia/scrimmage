# Display Units Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** one Units setting, Imperial (default) or Metric, governs every value the web app shows or takes and every
server-built message, while files, JSON, CSV and MCP stay as they are.

**Architecture:**
- **One shared table:** `scrimmage/units.py` holds the table (kind → unit name, factor from SI, decimals, per
  system). It serves the table to the page.
- **One page helper:** `static/units.js` formats and parses with it.
- **The setting** lives in `scrimmage/settings.py` (`settings.toml` in the user config dir). The server re-reads it
  on change, and the page re-renders when it changes.
- **The work:** after Task 1, each later task converts one area's sites to the helpers.

**Tech Stack:** Python 3.11+ (tomllib, plus tomli_w or the repo's TOML writer: use what the repo already writes TOML
with), vanilla JS, Playwright browser tests, pytest.

**Spec:** docs/superpowers/specs/2026-11-04-display-units-design.md (Goals 1–6, decisions U1–U5 are binding).

## Global Constraints

- **The setting:** `units = "imperial" | "metric"` in `<user config dir>/scrimmage/settings.toml`.
  `$SCRIMMAGE_CONFIG` names the settings directory instead. The default is "imperial".
- **The table (spec Goal 2):**
  - **length:** in / mm;
  - **field:** ft / m;
  - **speed:** ft/s / m/s;
  - **mass:** lb / kg;
  - **force:** lbf / N;
  - **torque:** in·lbf / N·m;
  - **pressure:** psi / kPa;
  - **density:** lb/in³ / g/cm³;
  - **spring:** lbf/in / N/m;
  - **unchanged in both systems:** angle °, time s, current A, voltage V, and angular speed as shown today.
- **Files untouched:**
  - an unedited file is byte-identical whatever the setting;
  - a key with a unit in its name (`_in`, measure suffixes) keeps that unit;
  - MCP results, `report.json`, CSVs and saved files stay SI.
- **Tests that assert displayed text** set the units explicitly, through a `units` pytest fixture that points
  `$SCRIMMAGE_CONFIG` at a temporary directory. Add it to tests/conftest.py.
- **Wording and tests:** American spellings and plain words. Never run `uv run` in the repo root; use
  `.venv/bin/python -m pytest`, serially.

## Review Focus

1. **A typed value round-trips.** Type 4 in, save, reload, and it shows 4 in. Switch to metric and it shows 101.6 mm,
   and the file is unchanged by the switch. Test in every task that converts an input.
2. **An `_in` key edited in metric:** typing 101.6 mm writes `diameter_in = 4.0`, not a meters key (U3, Goal 3).
   Test: Task 4 (Bench) and Task 3 (Test tab).
3. **A settings file that is missing, unreadable or has a bad value:** this falls back to imperial with one plain
   warning, never a crash. Test: Task 1.
4. **Switching while a tab is open:** the open tab re-renders in the new units without a reload, and a half-typed
   input isn't lost (U2). Test: Task 1 (the mechanism) and Task 2 (a real tab).
5. **Negative, zero and huge values, and values typed with a unit** ("4 in", "100mm"): these parse, or say plainly
   which unit is expected. Test: Task 1.

---

### Task 1: The units core, the settings store, and the Settings menu

**Files:**
- Create: `scrimmage/units.py`, `scrimmage/settings.py`, `scrimmage/assembler/static/units.js`, `tests/test_units.py`,
  `tests/test_settings.py`
- Modify:
  - `scrimmage/assembler/server.py`: `GET /api/settings` gives `{units, table}`, and `POST /api/settings` takes
    `{units}`. Settings changes reach the page through the existing state or stream mechanism, or a small poll;
    pick one and say why.
  - `static/index.html`, `app.js` and `app.css`: a Settings menu in the header with Units Imperial/Metric, built so
    20b can add Language and Theme rows.
  - `scrimmage/__main__.py`: a global `--units`.
  - `tests/conftest.py`: the `units` fixture.

**Interfaces (produced):**
- **`scrimmage/settings.py`:**
  - `settings_dir() -> Path` follows `$SCRIMMAGE_CONFIG`, else platformdirs-style per OS (use `platformdirs` if
    it's already a dependency; else implement XDG / `%APPDATA%` / `~/Library/Application Support`).
  - `load() -> Settings(units: str)` falls back to the default with a warning (Review Focus 3).
  - `save(**changes)` writes atomically.
  - `current_units() -> str` honors a process-wide override set by `--units` (`set_override(units)`).
- **`scrimmage/units.py`:**
  - `KINDS`, and `TABLE: dict[system][kind] -> Unit(name, factor_from_si, decimals)`;
  - `fmt(value_si, kind, system=None) -> str`, for example "4 in" or "101.6 mm", trimming trailing zeros within the
    decimals;
  - `to_display(value_si, kind, system=None) -> float`;
  - `from_display(value, kind, system=None) -> float` (SI);
  - `unit_name(kind, system=None)`;
  - `table_json()`, sent to the page.
- **`static/units.js`:**
  - `units.system`, plus `fmt(si, kind)`, `toDisplay`, `fromDisplay` and `name(kind)`;
  - `parse(text, kind) -> si | {error}`, which accepts a bare number or a number with a matching unit name, and
    refuses the other system's unit with a plain message;
  - `onChange(fn)`, for re-rendering;
  - it loads the table from `/api/settings`.
- **Converting a file's own unit keys:** `units.js` `toKey(si, keyUnit)` and `fromKey(v, keyUnit)` handle `_in`
  keys and the measure suffixes (U3).

- [ ] **Step 1: Write the failing tests:**
  - `fmt` and `parse` for every kind in both systems, with the spec's examples (4 in, 0.25 in, 12.5 ft, 101.6 mm,
    60 psi → 413.7 kPa as shown);
  - round trips;
  - Review Focus 3 and 5;
  - `--units` overriding the file;
  - the API GET and POST;
  - a browser test: the Settings menu switches the system, and a test element using `units.onChange` re-renders
    without a reload (Review Focus 4).
- [ ] **Step 2–4: Implement and run** them plus `-k "server or app or browser or cli"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "display units: one Units setting, a shared unit table, and a Settings menu"`

### Task 2: The Assembler, Part, Drive and Scenario tabs

**Files:**
- Modify (the sites the survey listed):
  - `static/app.js` (CG and mass readouts);
  - `inspector.js` (mount offsets, nudge, stock params via `UNIT`);
  - `part.js` and `part_inspector.js` (CoG, mass, density, pivot, slide range, springs and dampers; the spool's own
    in/mm toggle is replaced by the setting);
  - `readouts.js` (pose: field; speed);
  - `code.js` and `fieldpick.js` (start pose and target: field);
  - `step_editor.js` and `scenario.js` (x, y, tol_xy, max: field; camera distance: field);
  - `drive.js` (push force);
  - `report.js` (the run report's positions and speeds).
  Stock part `Param` units (parts/stock/*.py: `N`, `N*m`, `N/m`, `kg*m^2`) map to kinds, and an unmapped one
  (`kg*m^2`, `m^3/(s*Pa)`) is shown as today.
- Test: the existing browser test files for these tabs. Add a units case to each, and set `units` explicitly in the
  existing text asserts that now depend on it.

**Interfaces:**
- **Consumes:** Task 1's `units.js` and the fixture.
- **Each site:** shows `units.fmt(si, kind)`. Inputs show `units.toDisplay`, take `units.parse`, and save SI as today.
- [ ] **Step 1: Write the failing tests:**
  - per tab, one shown value and one typed value in both systems;
  - Review Focus 1 (round trip) on a part's mass and a scenario step's x;
  - Review Focus 4: switching while the Part tab is open.
- [ ] **Step 2–4: Implement and run** them plus `-k "assembler or part or drive or scenario or report or browser"`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "display units: the Assembler, Part, Drive and Scenario tabs"`

### Task 3: The Test tab and its server text

**Files:**
- Modify:
  - `static/test.js` and `test_run.js` (`IN` constants go; signals' `unitOf`; chart labels; the release box);
  - `blocks_defs.js` (block labels like "at least %2 in"; the unit dropdown shows the system's names, U4);
  - `scrimmage/blocks/run.py` (`_amount`, failure messages, and signals: keep signal values SI on the wire and let
    the page convert, or send them in display units with the unit named. Pick one, and say why);
  - `scrimmage/blocks/placed.py` and `scrimmage/blocks/feeds.py` (sentences);
  - `scrimmage/assembler/view.py` (`threshold_in` etc. stay file keys).
- Test: `tests/test_blocks_*.py` failure-text asserts (set units), the Test tab browser tests, and the existing
  shared run-text tests

**Interfaces:**
- **Consumes:** Task 1's `units.py` and `units.js`.
- **Server text:** built with `units.fmt(..., system=current_units())` when the run is made (U2).
- **Typing in a key's own unit:** a Test-file `_in` key typed in metric is converted into inches when written
  (Review Focus 2).
- [ ] **Step 1: Write the failing tests:**
  - a failure message ("seated 1.5 in of 2 in") in both systems;
  - a block label in both systems;
  - the signal chart's axis unit in both systems;
  - a typed `ahead_in` round trip in metric;
  - pressure "60 psi" vs "413.7 kPa".
- [ ] **Step 2–4: Implement and run** them plus `-k "blocks or test_tab or test_session or browser"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "display units: the Test tab and its messages"`

### Task 4: The Bench, the Measure tab, and server text elsewhere

**Files:**
- Modify:
  - `static/bench.js` and `bench_edit.js` (the `_in` twin display follows the setting; typed values are written in
    the key's existing form, U3);
  - `scrimmage/bench/report.py` (headlines, U5), `bench/pictures.py` and `bench/compare.py` (chart axis labels and
    tables);
  - `static/measure.js` (display follows the setting; the per-column unit picker stays, because it's the file's
    key suffix, and its default for a new column follows the setting);
  - `scrimmage/checks.py` and `scrimmage/scenario_runner.py` (report text: field and angle);
  - `scrimmage/measure/fit_*.py` messages.
- Test: the bench, measure and scenario-report test files (set units where text is asserted)

**Interfaces:**
- **Consumes:** Task 1.
- **Text and charts:** `summary.txt` and `report.md` text, and the PNG chart labels, use `current_units()` when
  written. JSON and CSV are unchanged.
- [ ] **Step 1: Write the failing tests:**
  - **Bench:** a headline in both systems; a chart axis label in both (read the chart's label strings, not pixels);
    Review Focus 2 on a Bench `_in` key typed in metric.
  - **Scenario report:** a check message in feet vs meters.
  - **Measure:** a new column's default unit follows the setting.
- [ ] **Step 2–4: Implement and run** them plus `-k "bench or measure or scenario or checks or report"`. Expected:
  PASS.
- [ ] **Step 5: Commit:** `git commit -m "display units: the Bench, the Measure tab, and report text"`

### Task 5: Sweep and docs

**Files:**
- Modify:
  - any display site still hard-coding a unit (grep the static JS for `" m"`, `" in"`, `" kg"`, `"psi"`, `m/s`,
    `" N"`, and the Python text sources);
  - docs: a "Units" section in `docs/assembler.md` (or a new `docs/settings.md` that 20b will extend);
  - `README.md` (`--units`, and that MCP and JSON stay SI);
  - the spec's decisions with any rulings.
- Create: `tests/test_units_sweep.py`. It scans the static JS for hard-coded unit literals and fails on new ones,
  with an allowlist for the sites that are allowed: angles, time, A, V, and file-key names.
- [ ] **Step 1:** Run the sweep, and fix or allowlist each hit with a reason.
- [ ] **Step 2:** Write the docs, with a docs test that names the setting, both systems, the table and `--units`.
- [ ] **Step 3:** Run the full suite once. Expected: PASS.
- [ ] **Step 4: Commit:** `git commit -m "display units: a sweep for leftover units, and docs"`
