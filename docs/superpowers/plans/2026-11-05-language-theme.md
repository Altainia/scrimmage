# Language and Theme Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- **Language:** a Language setting (en-US default, en-GB), with every user-visible web-app string translatable
  through catalogs that other teams can extend.
- **Theme:** System, Light or Dark.
- **Radii:** smaller border radii.

**Architecture:**
- **Translation calls:** `scrimmage/i18n.py` and `static/i18n.js` provide `t(source, params)` and
  `tn(singular, plural, count, params)`. Keys are the American English source text, with named `{placeholders}`.
- **Catalogs:** `scrimmage/locales/<lang>.json` hold only the differing entries, and languages are found by
  scanning that folder.
- **Setting and machine text:** the setting lives in `scrimmage/settings.py` beside `units`. 20a's machine-text
  guards also fix the language to en-US.
- **Theme:** a `data-theme` attribute plus one `static/theme.js` helper, which every theme reader uses.
- **Wrapping:** after Tasks 1–2, Tasks 3–5 wrap one area each, and Task 6 fills en-GB, adds the gate and writes the
  docs.

**Tech Stack:** Python 3.11+ (tomlkit for settings, as 20a), vanilla JS with `Intl.PluralRules`, Blockly, Playwright
browser tests, pytest.

**Spec:** docs/superpowers/specs/2026-11-05-language-theme-design.md (Goals 1–6, decisions L1–L8 are binding).

## Global Constraints

- **Settings:** `language = "en-US" | <a catalog's tag>` and `theme = "system" | "light" | "dark"` in
  `settings.toml`. The defaults are "en-US" and "system". A bad value falls back to the default with one warning.
- **Defaults keep today's text:** with en-US and System, every string reads exactly as today, so existing English
  test assertions pass unchanged. The only visible change is the smaller radii.
- **Machine output stays en-US:** CSV, JSON, MCP results, the Test tab's machine text, and report.json, through
  20a's guards (L2).
- **Files and keys are never translated:** TOML keys, status keys (`"cancelled"`), CSS classes and data
  attributes.
- **A change applies live:** a Language or Theme change applies to the open page without a reload and keeps
  half-typed input. For Blockly, follow L7.
- **Wording and tests:** American spellings and plain words in the source. Never run `uv run` in the repo root; use
  `.venv/bin/python -m pytest`, serially. Tests that check en-GB or a theme set it explicitly; the conftest fixture
  already isolates settings.

## Review Focus

1. **A wrapped string with placeholders, translated in a different order:** en-GB, or a test catalog, reorders
   `{a} of {b}`. Both values land in the right places. Test: Task 1.
2. **A catalog entry with a wrong placeholder or a missing plural form:** it falls back to English for that string,
   warns once, and never crashes. `i18n check` reports it. Test: Task 1.
3. **Switching the language while a box is half typed or a Blockly field editor is open:** nothing is lost. Test:
   Task 4 (Blockly) and Task 3 (a plain box).
4. **System theme with the OS flipping to dark, while Light is chosen:** the page stays light. With System chosen,
   it follows the OS live. Test: Task 2.
5. **An agent's run while the room is in en-GB:** the MCP result and CSV text are American English, byte-identical
   to before. Test: Task 1 (the guard) and Task 4 (a real run_test).

---

### Task 1: The translation core, the Language setting, and the tool

**Files:**
- Create:
  - `scrimmage/i18n.py`, `scrimmage/assembler/static/i18n.js`;
  - `scrimmage/locales/en-GB.json` (just `_language` and two or three entries, to start);
  - `scrimmage/locales/en-GB.spellings.json` (the L4 word pairs);
  - `tests/test_i18n.py`, `tests/test_i18n_browser.py`.
- Modify:
  - `scrimmage/settings.py` (`language`, discovered languages);
  - `scrimmage/units.py` (the machine guards also fix the language; or move the guard to a shared spot that both
    use);
  - `scrimmage/assembler/server.py` (`/api/settings` gains `language`, `languages` and the catalog);
  - `static/app.js` and `index.html` (a Language row in the Settings menu, and `<html lang>` that follows it);
  - `scrimmage/__main__.py` (`--language`, and `scrimmage i18n check|template`).

**Interfaces (produced):**
- **Python (`scrimmage/i18n.py`):**
  - `t(source: str, /, *, lang: str | None = None, context: str | None = None, **params) -> str`;
  - `tn(singular, plural, count, /, *, lang=None, context=None, **params) -> str`, where `{count}` is available;
  - `current_language()`, `languages() -> dict[tag, name]`, `plural_category(lang, n) -> str`;
  - `catalog_json(lang)`, for the page.
- **JS (`static/i18n.js`):** `t(source, params?)`, `tn(singular, plural, count, params?)`, `i18n.lang`,
  `onChange(fn)`, plus `i18n.setLanguage` and `i18n.languages`. It loads with `/api/settings`, as units.js does.
- **Catalog format:**
  - `{"_language": "English (UK)", "<source>": "<translation>"}`;
  - plurals as `"<singular>|<plural>": {"one": "...", "other": "..."}`;
  - context as `"<context>\u0004<source>"`.
- **The tool:**
  - `scrimmage i18n template [--out FILE]` lists every wrapped source string with its file:line. The sources come
    from scanning the code for `t(`/`tn(` calls with literal first arguments; a non-literal first argument is an
    error the sweep reports.
  - `scrimmage i18n check [LANG]` lists missing, unused and placeholder-mismatched entries. It exits 1 on mismatches
    and 0 on missing-only.

- [ ] **Step 1: Write the failing tests:**
  - `t`/`tn` fall back to English; placeholders, reordering and plural categories work for en (Review Focus 1);
  - a broken entry warns once (Review Focus 2);
  - languages are discovered, and a bad setting falls back;
  - `--language` overrides the setting;
  - the machine guard forces en-US (Review Focus 5);
  - the tool's `template` and `check` give the right output on a fixture source tree;
  - browser: the Language row switches, `<html lang>` follows, and a demo `t()` element re-renders live.
- [ ] **Step 2–4: Implement and run** them plus `-k "settings or units or server or cli"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "language: translation calls, catalogs, a Language setting, and the i18n tool"`

### Task 2: The theme toggle and smaller radii

**Files:**
- Create: `static/theme.js`, `tests/test_theme_browser.py`.
- Modify:
  - `scrimmage/settings.py` (`theme`);
  - `/api/settings`;
  - `index.html` and `app.js` (a Theme row: System, Light, Dark);
  - `app.css`: the dark tokens apply under `:root[data-theme=dark]`, and also under
    `@media (prefers-color-scheme: dark) { :root[data-theme=system] }`. Add the radius tokens from L8, and replace
    every literal radius.
  - every JS theme reader goes through `theme.isDark()` and `theme.onChange`: test.js (the Blockly theme), part3d.js
    (the background) and test_run.js (charts);
  - canvas colors read CSS tokens through a small `theme.color("--token")` helper: trackplot.js, fieldpick.js,
    readouts.js, code.js and minifield.js;
  - bench/gallery.py's standalone CSS uses the same radii.

**Interfaces:**
- **Produces:** `theme.isDark()`, `theme.onChange(fn)` and `theme.color(name)`. Later tasks use them.
- [ ] **Step 1: Write the failing tests:**
  - **The toggle:** each setting applies; with System chosen, an emulated OS dark/light change follows live
    (Playwright `emulate_media`); with Light chosen, an OS dark change doesn't apply (Review Focus 4).
  - **Every theme reader** follows a switch: Blockly's theme name, the 3D background color and a chart's colors.
  - **Radii:** no literal `border-radius` other than `50%`/`0` is left in app.css (a small CSS scan), and
    `--radius` is 4px.
- [ ] **Step 2–4: Implement and run** them plus `-k "browser"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "theme: System, Light or Dark in Settings, and smaller radii"`

### Task 3: Wrap the Assembler, Part, Drive, Robot code and Start pages

**Files:**
- Modify:
  - `index.html`. Static text gets `data-i18n` (and `data-i18n-title`, `-placeholder`, `-aria-label`) attributes,
    and i18n.js fills them in and refills them on a change.
  - `static/app.js`, `inspector.js`, `part.js`, `part_inspector.js`, `part3d.js`, `drive.js`, `code.js`,
    `readouts.js`, `fieldpick.js`, `minifield.js`, `start.js`, and any other file these tabs use;
  - the server text these tabs show: `scrimmage/assembler/server.py` and `edits.py` messages, `part_session.py`,
    config errors shown on the page, and parts/stock param labels.
- Test: the existing browser test files for these tabs (English asserts stay valid), plus per-tab en-GB checks.

**Interfaces:**
- **Consumes:** Task 1's `t`/`tn` (both sides) and `i18n.onChange`; Task 2's theme helper.
- **Rules for wrapping:**
  - Use whole sentences with named placeholders, never pieces joined in code.
  - Plurals go through `tn`.
  - L3: wherever the page matches on server text, switch it to a code.
  - Canvas text is redrawn on a change.
- [ ] **Step 1: Write the failing tests:**
  - **Per tab:** in en-GB, one translated string shows and a placeholder sentence reads right.
  - **Review Focus 3:** a half-typed inspector box survives a language switch.
  - **Server text:** one validation message from the server shows in en-GB.
- [ ] **Step 2–4: Implement and run** them plus `-k "assembler or part or drive or robot_code or start or browser"`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "language: the Assembler, Part, Drive, Robot code and Start pages"`

### Task 4: Wrap the Test tab and its server text

**Files:**
- Modify:
  - `static/test.js`, `test_run.js` and `blocks_defs.js`: block messages, tooltips and dropdown labels; on a
    change, re-register the blocks and rebuild the workspace (L7);
  - `scrimmage/blocks/run.py`, `program.py`, `palette.py`, `conditions.py`, `placed.py`, `feeds.py`, and the human
    text of `sweep.py`/`spread.py`;
  - `scrimmage/assembler/test_session.py`.
- Test: the Test tab browser tests and the blocks tests (English asserts stay valid), plus en-GB checks.

**Interfaces:**
- **Consumes:** Task 1 and the machine guards.
- **Block messages:** `%1` placeholders stay positional inside each block message.
- [ ] **Step 1: Write the failing tests:**
  - en-GB shows a translated block label and a translated failure message;
  - **Review Focus 3:** a Blockly field editor open during a switch keeps its value;
  - **Review Focus 5:** an MCP `test_run` with the room in en-GB returns byte-identical text to en-US;
  - the sweep and spread `table.csv` text is unchanged in en-GB.
- [ ] **Step 2–4: Implement and run** them plus `-k "test_tab or blocks or testrange or mcp"`. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "language: the Test tab and its messages"`

### Task 5: Wrap the Scenario, Bench and Measure tabs, the run report, and their server text

**Files:**
- Modify:
  - `static/scenario.js`, `step_editor.js`, `steps.js`, `report.js`, `bench.js`, `bench_edit.js` and
    `measure.js`;
  - `scrimmage/checks.py` (shown text only), `scenario_runner.py` (report.md and shown text only),
    `bench/report.py` (summary.txt, headlines shown and chart labels), `bench/gallery.py`, and
    `measure/forms.py`/`fit_*.py` (human text).
- Test: the existing browser and report tests (English asserts stay valid), plus en-GB checks.

**Interfaces:**
- **Consumes:** Task 1 and the machine guards. report.json and the MCP text stay en-US.
- [ ] **Step 1: Write the failing tests:**
  - en-GB shows a translated string in each tab;
  - report.md written in en-GB uses en-GB;
  - report.json and the MCP scenario and bench text are unchanged in en-GB.
- [ ] **Step 2–4: Implement and run** them plus `-k "scenario or bench or measure or report or checks or mcp"`.
  Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "language: the Scenario, Bench and Measure tabs and report text"`

### Task 6: The en-GB catalog, the gate, and docs

**Files:**
- Modify: `scrimmage/locales/en-GB.json`. Fill it from the template using the spellings list and a read of the
  template.
- Create:
  - `tests/test_i18n_sweep.py`: the L1 gate. It fails on unwrapped user-visible literals in static JS/HTML and the
    Python text sources, with an allowlist that gives a reason per entry.
  - `tests/test_en_gb_catalog.py`: L4 completeness, plus `i18n check en-GB` reports no mismatches and no unused
    entries.
  - `docs/translating.md`: the contributor guide. It covers adding a language file, the template, `i18n check`,
    plurals, context, what is never translated, and testing a translation.
- Modify:
  - `docs/settings.md`: add Language and Theme sections;
  - `README.md`: `--language` and the settings;
  - the spec's decisions, with any build rulings.
- [ ] **Step 1:** Run the sweep. Wrap or allowlist each hit, with a reason.
- [ ] **Step 2:** Fill en-GB until the completeness test passes. Read the template once for British usage beyond
  the spelling list, and note what you chose.
- [ ] **Step 3:** Write the docs, with a docs test like `tests/test_settings_docs.py`.
- [ ] **Step 4:** Run the full suite once. Expected: PASS.
- [ ] **Step 5: Commit:** `git commit -m "language: the British English catalog, a gate for unwrapped text, and docs"`
