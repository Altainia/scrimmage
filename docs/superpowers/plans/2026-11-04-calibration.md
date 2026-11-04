# Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measurement files for six tests, averaging with spread warnings, model fits for the launcher and cylinder,
measurements usable wherever their numbers are used, specific provenance labels, illustrated how-to pages,
printable forms, and a Measure tab.

**Architecture:**
- **New package:** `scrimmage/measure/` holds units, the file format, one module per test turning trials into a
  value, and the launcher and cylinder fits.
- **Provenance:** a small `Provenance` record (file, by, date, trials, good) travels with each measured input. The
  report layers name estimates against measurements from it.
- **Forms:** the printable forms and the web tab use the same SVG illustrations and the same test definitions.

**Tech Stack:** Python 3.11+, numpy, the launcher model (scrimmage/launcher), the pneumatic model
(scrimmage/pneumatics), the web app (scrimmage/assembler), pytest and Playwright.

**Spec:** docs/superpowers/specs/2026-11-04-calibration-design.md

## Global Constraints

- No new dependencies (no scipy).
- A file or config that uses no measurement gives byte-identical output, except the UNCALIBRATED line's wording.
  Existing tests change only where they assert that line.
- Every reading's key ends in a unit. The allowed units are `n`, `lb`, `kg` (as force), `g`, `oz`, `in`, `mm`, `cm`,
  `m`, `s` and `frames`. A missing or unknown unit errors with the allowed list.
- **Averaging:** at least 3 trials, and a form has room for 8. The spread is (max − min) / mean. Over 15% warns. An
  outlier is a trial more than twice the mean absolute deviation from the mean; it is flagged, never dropped.
- **Fits:** run the simulator's own models. A fit is "good" only under its accuracy limit, with no fitted number
  within 5% of a search bound. A fit that isn't good never removes UNCALIBRATED.
- **Pictures and print:** SVGs are black-and-white legible, and forms print on US Letter and A4 with no scripts.
- **Wording and tests:** American spellings and plain words. Never run `uv run` in the repo root; use
  `.venv/bin/python -m pytest`, serially.

## Review Focus

1. **Two trials of very different size** (5 lb and 15 lb). The average is reported, with "take at least 3 trials"
   and no value used in a run (the file reads as not good enough). Test: Task 1.
2. **A launcher file with shots at one wheel speed only:** only stiffness is fit, and the report says why. Test:
   Task 2.
3. **A fit pinned at its search limit:** reported, flagged "at the edge of what it tries: check your shots", and the
   UNCALIBRATED label kept. Test: Task 2.
4. **A ball setting pointed at a friction file:** an error naming both the setting and the file's kind. Test:
   Task 4.
5. **A measurement file moved or deleted after a setup names it:** the error names the setup, the key and the path
   it looked for. Test: Task 4.

---

### Task 1: Measurement files, units and the direct tests

**Files:**
- Create: `scrimmage/measure/__init__.py`, `units.py`, `file.py`, `stats.py`, `tests_direct.py`
- Test: `tests/test_measure_direct.py`

**Interfaces:**
- **Units:** `units.reading(row: dict, quantity: str) -> float` (SI). The quantity is one of "force", "mass",
  "length", "time" or "frames". It finds the key `<name>_<unit>` and errors on none, several, or an unknown unit.
- **The file** (`file.load(path) -> MeasureFile`):
  - **Fields:** `kind ("ball" | "friction" | "launcher" | "cylinder"), about: str, by: str, date: str (ISO),
    tools: str, base: str | None` (a ball's or wheel's preset), `trials: dict[test, list[dict]]`, `path`.
  - **TOML shape:** `[measure] kind, about, by, date, tools, base`, then one array of tables per test: `[[size]]`,
    `[[squeeze]]`, `[[drop]]`, `[[sled]]`, `[[shot]]`, `[[stroke]]`, plus each test's own settings table
    (`[shot_setup]`, `[stroke_setup]`).
- **Stats:** `stats.summary(values) -> Summary(mean, low, high, spread, n, outliers: list[int], warnings:
  list[str], good: bool)`. `good` means n ≥ 3 and spread ≤ 15%.
- **Direct tests:** `tests_direct.values(mf) -> dict[field, (Summary, per_trial: list[float])]`.
  - **Ball:** `mass` and `diameter` (circumference/π, or a direct diameter); `stiffness_n` (C5);
    `restitution` (C6).
  - **Friction:** `friction` (C10).
- **Provenance:** `Provenance(file, by, date, trials, good)`, from `file.provenance(mf, summary)`.
- [ ] **Step 1: Write the failing tests:**
  - **Units:** `force_lb = 10` is 44.48 N; `mass_oz = 8` is 0.2268 kg; `length_mm = 150` is 0.15 m; `force` with
    no unit errors listing the units.
  - **Squeeze at 75%:** five trials of [9.8, 10.1, 10.0, 9.9, 10.2] lb, minus a 1.0 lb board, average 8.999 lb
    (40.03 N, rel 1e-3). Good.
  - **Squeeze at another height:** to 80% with exponent 1.5 converts by (0.25/0.20)^1.5.
  - **Drop:** an 18 in drop and a 6.48 in bounce give restitution 0.6. A carpet floor warns.
  - **Sled:** a 2.0 lb pull on a 5.0 lb sled gives 0.4.
  - **Review Focus 1:** two trials give "take at least 3 trials" and good = False. Ten trials with one 2× the rest
    flag it as an outlier.
  - **Size:** a circumference of 18.85 in gives a diameter of 6.0 in.
- [ ] **Step 2–4: Implement and run** them. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "measure: measurement files, units, averaging and the direct tests"`

### Task 2: The launcher shot fit

**Files:**
- Create: `scrimmage/measure/fit_launcher.py`
- Test: `tests/test_measure_launcher_fit.py`

**Interfaces:**
- **`[shot_setup]`:** `setup = "<launcher setup file>"` (the launcher being measured, relative to the measure
  file).
- **`[[shot]]`:** each shot has `wheel_rps`, `hood_deg` (optional, defaulting to the setup's), a tape mark spacing
  (`marks_in`/`marks_mm`), `frames` and `fps`.
- **`fit(mf) -> LauncherFit`:**
  - fields: `stiffness_n, friction, rms_error, per_shot: list[(measured, model)], fitted: tuple[str, ...]`;
  - warnings: `list[str]`, and `good: bool`.
  - The search follows C8, running `scrimmage.launcher.model.Launcher` exactly as `scrimmage launcher run` does for
    one ball (reuse its code path; read `scrimmage/launcher/run.py`).
- [ ] **Step 1: Write the failing tests.**
  - **Synthetic shots:** use the model itself with the 2026-fuel ball at `stiffness_n = 120` and wheel friction 1.1
    as "truth", at 50 and 80 rps. The fit recovers both within 5% from the default preset start.
  - **Review Focus 2:** one wheel speed only fits stiffness alone, with the sentence.
  - **Review Focus 3:** shots faster than any setting can give are flagged "at the edge", and good = False.
  - **Time limit:** a fit of 6 shots finishes within 20 s on the dev machine (`pytest.mark.slow` only if the repo has
    that mark).
- [ ] **Step 2–4: Implement and run** them. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "measure: fit a launcher's ball stiffness and wheel friction to filmed shots"`

### Task 3: The cylinder stroke fit

**Files:**
- Create: `scrimmage/measure/fit_cylinder.py`
- Test: `tests/test_measure_cylinder_fit.py`

**Interfaces:**
- **`[stroke_setup]`:** `bore`, `rod`, `stroke` (unit-suffixed lengths), `load_mass` (optional), `vertical` (bool,
  default false), `working_psi` (60), `friction_n` (8).
- **`[[stroke]]`:** each stroke has `time_s` (or `frames` and `fps`).
- **`fit(mf) -> CylinderFit(conductance, model_time, measured_time, error, warnings, good)`:** C9. It uses
  `scrimmage.pneumatics.model.AirSystem` and a one-body rod at a 1 ms step.
- [ ] **Step 1: Write the failing tests:**
  - **Synthetic recovery:** the model's own stroke time at 2× the default conductance is fit back within 3%.
  - **Edge:** a stroke time shorter than the model gives at 10× conductance is flagged "at the edge", good False.
  - **Vertical load:** a vertical stroke with a load is slower than a horizontal one.
- [ ] **Step 2–4: Implement and run** them. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "measure: fit a cylinder's valve flow to its timed stroke"`

### Task 4: Using measurements, and provenance

**Files:**
- Create: `scrimmage/measure/use.py`
- Modify: `scrimmage/launcher/inputs.py` (`[ball] measured`, `[wheels] measured`), `scrimmage/testrange/spec.py` or
  wherever a range's ball is chosen (`ball = "<file>.measure.toml"`), `scrimmage/parts/grip.py` (a grip may be a
  `.measure.toml` path), piece surface friction (`friction = "<file>.measure.toml"` in a `[piece.*]` or
  `[surface.*]`), `scrimmage/parts/stock/mechanisms.py` (cylinder `measured`, Param kind text)
- Test: `tests/test_measure_use.py`

**Interfaces:**
- **Applying a file:** `use.apply_ball(fields: dict, path, where) -> (fields, dict[field, Provenance])`, and
  likewise `apply_wheels`, `friction_value(path, where) -> (float, Provenance)` and `apply_cylinder(params, path,
  where)`.
  - **Ball:** a ball file replaces the measured fields only. Its `base` must equal the setting's preset when both
    are given.
  - **Launcher file:** its fit gives a ball `stiffness_n` and a wheel `friction`, when good. A launcher file can be
    named by both `[ball] measured` and `[wheels] measured`.
  - **Not good enough:** a file that isn't good is still used, but its provenance is marked not good. The labels
    (Task 5) then keep UNCALIBRATED.
- **Kept with the inputs:** `Setup.provenance: dict[str, Provenance]` on the launcher setup, keyed "ball.stiffness_n",
  "wheels.friction" and so on. A grip's provenance goes on the part's grips, and a cylinder's on its `CylinderSpec`
  (a new optional field).
- **Errors** (Review Focus 4 and 5): "<where>: <key> = \"<path>\" is a friction measurement; a ball needs a ball or
  launcher measurement", and "<where>: <key> = \"<path>\": no such file (looked in <resolved>)".
- [ ] **Step 1: Write the failing tests** for each place a file can be named, the two errors, the base mismatch,
  provenance carried through, and an existing setup with no measurement parsing identically (same dataclass).
- [ ] **Step 2–4: Implement and run** them plus `-k "launcher or grip or range or stock or golden"`. Expected:
  PASS.
- [ ] **Step 5: Commit:** `git commit -m "measure: use a measurement wherever its number is used, with provenance"`

### Task 5: Labels that say what was measured

**Files:**
- Modify: `scrimmage/launcher/report.py` (the line and the chart footer), `scrimmage/bench/report.py` and
  `pictures.py`, `scrimmage/blocks/run.py` (`run_notes`: grip and pneumatic notes), `scrimmage/pneumatics/budget.py`,
  `scrimmage/disc/report.py` (unchanged wording if no measurement), `scrimmage/mcp_server.py` (results carry the
  same line)
- Create: `scrimmage/measure/labels.py`
- Test: `tests/test_measure_labels.py`

**Interfaces:**
- **`labels.line(inputs: dict[str, Provenance | None], matters: tuple[str, ...]) -> str`:**
  - **No input measured:** the existing UNCALIBRATED wording, unchanged except that it now ends with what matters
    ("UNCALIBRATED: estimates from preset material numbers, not fitted to measurements (ball stiffness, bounce,
    wheel friction)").
  - **Some measured:** "UNCALIBRATED: still estimates: wheel friction. Measured: ball stiffness and bounce
    (fuel.measure.toml, J. Smith, 2026-11-10, 5 trials)."
  - **All measured and good:** "Calibrated from measurements: ...".
  - **Not good:** a measurement that isn't good counts as an estimate and says why ("not good enough: 2 trials").
- **What matters:**
  - launcher: `ball.stiffness_n`, `ball.restitution`, `wheels.friction`;
  - grip: each pad;
  - cylinder: `conductance`;
  - bench: its piece's stiffness and the friction it uses.
- [ ] **Step 1: Write the failing tests** for each of the three forms, and for each caller (launcher run report,
  bench line, Test-tab grip note, air budget) with and without a measurement. Every existing test asserting the old
  line is updated to the new wording, and nothing else changes in those outputs.
- [ ] **Step 2–4: Implement and run** them plus the full `-k "launcher or bench or blocks or air or disc or mcp"`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "labels: say which numbers are measured and which are still estimates"`

### Task 6: Illustrations and printable forms, and `scrimmage measure`

**Files:**
- Create: `docs/img/measure/{size,squeeze,drop,sled,shot,stroke}.svg`, `scrimmage/measure/forms.py`,
  `scrimmage/measure/cli.py`
- Modify: `scrimmage/__main__.py` (`measure` subcommand), `pyproject.toml` (package data for the SVGs if the forms
  embed them from the package; keep a single source: the SVGs live in `scrimmage/measure/img/` and docs reference
  copies made by a test-checked sync, or docs link the package path; pick one and say why)
- Test: `tests/test_measure_cli.py`, `tests/test_measure_forms.py`

**Interfaces:**
- **The illustrations:** each SVG shows the setup and what is read. For example, the squeeze shows a scale, two
  boards, the ball squeezed to 75% with the height marked, and "read the scale here". Line art: black on white,
  labels at least 10 pt when printed, and a viewBox for scaling.
- **`forms.form_html(test) -> str`:** a self-contained printable page.
  - **Header:** a title, what you need, and the illustration inline.
  - **Steps:** numbered and plain.
  - **Fields:** "Measured by / date / tools / what you measured".
  - **Trials table:** 8 rows with the test's reading columns, units printed, and a "value" column with its formula
    above.
  - **Summary boxes:** "average", "lowest", "highest", "spread %", and a tick box "spread under 15%? If not,
    measure again".
  - **Footer:** "Type these into Scrimmage's Measure tab, or a <name>.measure.toml file".
  - **Print rules:** `@page` rules for Letter and A4, and no scripts.
- **CLI:**
  - `scrimmage measure <file>` prints each test's trial table, the summary and warnings, and for launcher or
    cylinder files the fit. It exits 0 when good, 1 when not good, and 2 on a bad file.
  - `scrimmage measure form <test> [-o path]` writes the form (stdout without `-o`).
  - `scrimmage measure new <test> <path>` writes a starter `.measure.toml` with comments.
- [ ] **Step 1: Write the failing tests:**
  - every test has a form, with 8 rows, the units, the SVG inline, `@page` rules and no `<script>`;
  - each CLI subcommand, with its exit codes;
  - every SVG parses as XML, has a viewBox, and uses no color fill other than black, white and grays.
- [ ] **Step 2–4: Implement and run** them. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "measure: illustrations, printable forms and scrimmage measure"`

### Task 7: The Measure tab

**Files:**
- Modify: `scrimmage/assembler/server.py` (open `.measure.toml`; API: state, save, fit, form), `static/index.html`,
  a new `static/measure.js`, `static/start.js` (the "Measure something" entry), `static/app.css`
- Test: `tests/test_measure_browser.py`, `tests/test_measure_server.py`

**Interfaces:**
- **Opening:** `scrimmage edit <x>.measure.toml` opens the Measure tab.
  - **The test picker:** a list of the file's kind's tests. A ball file offers size, squeeze and drop; a friction
    file offers sled; a launcher file offers shot; a cylinder file offers stroke.
  - **For the chosen test:** the illustration, the steps, a header form (by, date, tools, about, base), a trials
    table (add, remove, type values with a unit picker per column), and live summary and warnings from the server's
    stats (the same code as the CLI).
  - **Buttons:** "Fit" (launcher and cylinder; shows the fit's numbers and each trial's measured against model),
    "Print a blank form" (opens the form page), and Save (writes the file with tomlkit, keeping comments). The Save
    prompt and dirty state work like the other tabs.
- **The start page:** "Measure something" asks which kind and a name, then creates `<name>.measure.toml` from the
  starter and opens it.
- [ ] **Step 1: Write the failing tests:**
  - the server opens the kind and lists its tests;
  - saving round-trips;
  - Fit returns the CLI's numbers;
  - in the browser: enter 5 squeeze trials and see the average, add an outlier and see the flag, save and reload,
    and the print form opens.
- [ ] **Step 2–4: Implement and run** them plus the browser tests. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "the Measure tab: enter trials, see the average and spread, fit, print a form"`

### Task 8: Docs, a worked example and the spec's decisions

**Files:**
- Create: `docs/measuring.md`, `examples/measure/` (a ball file with real-looking trials for the 2026 FUEL, a
  friction file, a launcher shot file using `examples/launcher`'s setup, a cylinder file, and a README)
- Modify: `docs/launcher.md`, `docs/test-tab.md`, `docs/pneumatics.md`, `docs/test-range.md`, `docs/bench.md`
  (point their measure-your-own text to measuring.md), `README.md` (the command), and the spec's "Decisions made
  during the build"
- Test: `tests/test_measure_examples.py` (each example file loads, summarizes, fits where it applies, and a launcher
  setup using it shows "Measured:" in its report)
- [ ] **Step 1:** Write docs/measuring.md: per test, the illustration, the steps, the form link, what it feeds, and
  how to use the file. Also how the averaging, spread and fits work, in plain words.
- [ ] **Step 2:** Add the examples and their tests, and update the pointers.
- [ ] **Step 3:** Run the docs tests, the example tests, and the full suite once. Expected: PASS.
- [ ] **Step 4: Commit:** `git commit -m "docs: measuring, with examples"`
