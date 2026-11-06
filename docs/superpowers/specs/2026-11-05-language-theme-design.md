# Language and Theme Design (sub-project 20b)

**Status:** the owner asked for this on 2026-11-04 and delegated design questions (decide-and-document,
2026-10-28). Written 2026-11-05. It follows 20a (Display Units, PR #12).

The owner's words: "For language localization, support American English and British English for now. (I envision
in the future, if teams from other countries start using this, they may assist in adding translations)." The
language setting is separate from Units, because FRC is imperial and some teams in metric countries may still pick
imperial. The owner also asked for a manual dark-theme toggle and smaller border radii.

## The survey (2026-11-05)

- **No translation system yet:** there is no catalog, `t()` helper, locale code or string id.
- **The page's text:** literal strings in about 30 JS files (about 1,300) and index.html (about 180), plus about
  1,500–2,000 Python strings the page shows (validation messages, run outcomes, check messages, headlines, report
  text).
- **How it's built:**
  - sentences are often assembled from pieces;
  - about 28 JS and 78 Python sites pluralize by hand;
  - canvas text is drawn with `fillText`;
  - Blockly blocks use positional `%1` messages.
- **en-GB differences are few:** about 60–100 strings (color, center, meter, aluminum, gray, fiber, modeled and
  neighboring). The status key `"cancelled"` is already British and is a key, not text. Dates are ISO or 24-hour,
  so en-GB needs no date or number format changes. The docs (about 104k words) are where most en-GB spellings live.
- **Tests:** about 1,300–1,600 of them assert exact English.
- **Theme:**
  - dark mode follows the OS through `@media (prefers-color-scheme: dark)` in app.css;
  - three JS sites (test.js Blockly theme, part3d.js background, test_run.js charts) read `matchMedia`;
  - about 60 CSS color tokens exist, but canvas colors in JS (trackplot, fieldpick and others) aren't themed.
- **Border radius:** `--radius: 6px` is used 51 times, and there are 56 literal radii (10px ×14, 4px ×13, 2px ×7,
  3px ×6, 8px ×3, 12px ×2, plus 50% for circles).

## Goals

1. **A Language setting:** American English (`en-US`, the default and today's text) or British English (`en-GB`).
   - **Where it lives:** a row in the Settings menu next to Units, `language` in `settings.toml`, and a CLI flag
     `--language`.
   - **It's separate from Units,** and neither changes the other.
   - **The page's `<html lang>`** follows it.
2. **Translation built for contributors.**
   - **Message catalogs:** every piece of user-visible text in the web app goes through a translation call. JS uses
     `t()` and `tn()` (plurals); Python uses the same pair.
   - **Keys are the American English text itself** (gettext style): untranslated text falls back to English, and
     wrapping can be checked automatically.
   - **Placeholders are named** (`{count}`, `{name}`), so a translator can reorder a sentence.
   - **Plural rules per language:** CLDR rules through `Intl.PluralRules` in JS and a small rule table in Python.
     English rules come now, and contributors add others.
   - **Catalogs:** `scrimmage/locales/<lang>.json`, one flat file per language, holding only the entries that
     differ from the source.
   - **A tool:** `scrimmage i18n check` lists text missing from a catalog, entries no longer used, and
     placeholders that don't match. `scrimmage i18n template` writes every source string for a new translation.
   - **A contributor guide:** `docs/translating.md`.
3. **What gets translated:** everything a student reads in the web app.
   - **On the page:** index.html, every tab's JS, Blockly block text and tooltips, canvas text, notices and errors.
   - **Server text the page shows:** validation and problem messages, run outcomes and check lines, Bench headlines,
     Measure text, and human report files (report.md, summary.txt, chart labels).
4. **The en-GB catalog** covers every en-US string with a British spelling, plus common British usage where it's
   plainly different. The catalog is reviewed by the translation tool, and a test keeps it complete for the
   spelling list.
5. **Theme: System | Light | Dark** in the Settings menu (`theme` in `settings.toml`), defaulting to System, which
   is today's behavior.
   - **How it works:** `data-theme` on `<html>`. CSS applies the dark tokens under `[data-theme=dark]`, or under
     System when the OS prefers dark.
   - **Every JS site that reads the theme** (Blockly, 3D background, charts and all canvas drawings) goes through
     one helper, and redraws on a change.
   - **Canvas colors** come from the CSS tokens.
6. **Smaller radii:** three radius tokens, `--radius-sm: 2px`, `--radius: 4px` (was 6px) and `--radius-lg: 6px`
   (for what is 8–12px today). Every literal radius becomes a token, except `50%` for circles and `0`.

## Non-goals

- **Translating the docs,** the CLI's printed text and `--help`, and MCP tool descriptions. They stay American
  English for now. The guide says docs translations are welcome as separate files later.
- **Machine output:** CSV, JSON, MCP results and the Test tab's machine text stay in American English, exactly as
  20a keeps their units. Agents and scripts see no change.
- **Right-to-left layout.**
- **Translating file keys and status keys** (`color`, `"cancelled"`).
- **Translating Blockly's own built-in menus.** Vendoring Blockly's message packs is for a later language that
  needs them.

## Constraints

- **Default behavior is unchanged:** with `en-US` and System theme, the app reads and looks as today, apart from
  the smaller radii. Existing tests asserting English stay valid without changes, because en-US is the default and
  the tests' settings folder is isolated.
- **One wording for each source string:** the en-US text is the key, so two places that mean different things with
  the same words share a translation. The tool supports an optional context (`t("Open", {context: "file"})`) for
  the rare clash.
- **Live changes:** a Language or Theme change applies to the open page without a reload, as Units does (U2 from
  20a), and keeps half-typed input. Text from a finished run keeps the language it was written in.
- **Wording and tests:** American spellings and plain words in the source. Tests use `.venv/bin/python -m pytest`,
  never `uv run` in the repo root.

## Left to the builder

- The catalog file format details (a flat JSON object `{source: translation}`, or `{source: {one, other}}` for
  plurals), and how the page loads it (alongside `/api/settings`).
- The order the tabs are wrapped in, as long as each task leaves the app working.
- How the Settings menu lays out three rows.

## Decisions (from the builder check)

Each decision gives the choice, the reason, and what it costs if it is wrong.

- **L1. Done means everything is wrapped, with a gate.**
  - **The full sweep:** this sub-project wraps all user-visible text in the web app and the Python text it shows.
  - **The gate:** a sweep test, like 20a's `test_units_sweep.py`, fails on an unwrapped user-visible literal in the
    static JS and HTML, and in the Python text sources the page shows. It keeps an allowlist with a reason per
    entry.
  - **Why:** contributors can only translate what's wrapped, and a gate stops the gaps coming back.
  - **Cost:** the biggest share of the work.
- **L2. The language is process-wide, and machine paths stay en-US.**
  - **`current_language()`** follows the setting and a `--language` override, the same as `current_units()`.
  - **Machine paths:** 20a's machine-text guards (`units.machine_text()`/`units.fixed()`) also fix the language to
    en-US, so every place that keeps machine units also keeps machine English.
  - **`t()`** reads the current language, and an optional `lang=` argument overrides it.
- **L3. The page must not match on server text.** Wherever JS compares a server message's text (to style it or
  branch on it), the server sends a code, and the page uses that. This is in scope, found by grep during wrapping.
- **L4. The spelling list** is a checked-in file, `scrimmage/locales/en-GB.spellings.json`, of American → British
  word pairs (color → colour, center → centre, meter → metre, aluminum → aluminium, gray → grey, fiber → fibre,
  modeled → modelled, and so on).
  - **The test:** every source string in the template (only wrapped text, so file keys like `color` and status keys
    like `"cancelled"` are never in it) that contains one of those words, whole-word and case-insensitive, must
    have an en-GB entry, and that entry must not contain the American word.
  - **Exceptions:** an allowlist covers words used as code inside a sentence, for example `color = "red"` shown as
    file syntax.
- **L5. Languages are found by scanning `scrimmage/locales/`.**
  - **The list:** en-US (the source, with no catalog) plus each `<lang>.json` catalog.
  - **Names:** each catalog names itself (`"_language": "English (UK)"`), and the menu, `check_language` and
    `--language` use that list.
  - **Adding a language** is one file.
- **L6. A broken entry falls back to English** for that string, with one warning per entry. Python logs it, and the
  page uses `console.warn` once per entry. `scrimmage i18n check` reports it too. A broken catalog never stops
  anything.
- **L7. Blockly on a language change:** commit any open field editor (as 20a does on a units change), re-register
  the block definitions in the new language, and rebuild the workspace from its saved state.
  - **Cost:** the cursor's place in the workspace may move. No typed value is lost, because the open editor is
    committed first.
- **L8. Radius mapping:**
  - 2px and 3px become `--radius-sm`, and 4px and 6px become `--radius`;
  - 8px, 10px and 12px become `--radius-lg` (6px), so panels get smaller, as the owner asked;
  - `50%` and `0` stay.

## Decisions made during the build

Each ruling from the build ledger (`.superpowers/sdd/2026-11-05-language-theme/progress.md`), with what it costs. An
amended ruling is listed as it stands, with what it replaced.

**How text is wrapped**

- **Literal sources only.** A t()/tn() source is one literal string, never a variable or text joined from pieces,
  so `scrimmage i18n template` can find it; the parts that change are {placeholders}. Cost: some assembled
  sentences were restructured into whole sentences.
- **One machine guard.** `i18n.fixed()` holds the guard; `units.fixed()` and `units.machine_text()` enter it, so
  one guard keeps machine units and machine English. Cost: nothing.
- **index.html's text** is named in `data-i18n*` attributes, which the template scans. Cost: a markup convention.
- **app.js locals named `t`** that hid the imported t() were renamed. Cost: nothing.
- **Text defined at import time** is marked with `N_()` and shown with `translated()` (stock part docs, constants).
  A t() at import would freeze the starting language; a gate test (test_i18n_import_time.py) checks for it. Cost:
  nothing.
- **Placeholders are checked against the call.** test_i18n_calls.py checks every t()/tn()/translated() call gives
  exactly its source's placeholders (Python AST, and JS object literals where practical), since the template tool
  can't. Cost: one test file.
- **What a student types is never translatable text.** Keys, values, `[table]` names and commands in a message are
  {placeholders} (`{kind_setting}` = `kind = "disc"`), so en-GB's "colour" never teaches a value the loader
  refuses; test_i18n_typed.py is the gate. Cost: about 450 calls restructured.
- **"(s)" plurals** in the loaders' messages ("table(s)") stay as written, so en-US is exact; translators may rewrite
  them (docs/translating.md says how). Cost: awkward plurals for translators.
- **describe()'s recast forms** ("waiting for…", "without…") were first left as English pieces inside translated
  sentences, then (0c6e465) became whole phrases, one per form. Cost: more catalog entries; no English pieces
  remain.
- **Server text students never see** (API-contract 400s the page never sends, internal assertions) may stay raw,
  each with a reasoned entry in the gate's allowlist. Cost: nothing.
- **List separators** (" and ", " or ") are translated with the context "list separator". Cost: nothing; the guide
  names the context.
- **The page loads the catalog only when it changes.** The settings poll sends a catalog version, and the page
  fetches the catalog when the language or version changes. Cost: nothing (a full translation would otherwise be
  sent every 2 s).

**Machine output (L2)**

- **The CLI tests in test_i18n.py** were changed to check that `--language` sets the setting and the CLI stays en-US,
  following the CLI ruling. Cost: nothing.
- **Two tasks were added** for what remained: 6c (the bench problem texts, Measure fit/CLI/stats text, part.toml,
  partner, field, overlay and range build errors, the bridge/robot-code/Java notes, and the Bench tab's UNCALIBRATED
  flag) and 6d (about 400 input-file loader messages, devicecheck and foreign writes), because L1 says done means
  wrapped and en-GB would otherwise show American spellings there. Cost: two more tasks.
- **"Shared with MCP/CLI" is no reason to leave text unwrapped.** Every MCP tool handler runs under
  `i18n.fixed("en-US")` through one wrapper, and the CLI's main does too, except `edit` and `serve`, which serve the
  web app. Shared text is then wrapped with t(). Cost: a bigger final stretch (Task 6 split into 6a–6d); MCP and CLI
  text stay byte-identical en-US, pinned by test_mcp_language.py and test_units_machine_text.py.
- **The CLI is American English throughout.** What `scrimmage run`/`test`/`bench` print, and the report.md and
  summary.txt they write, stay en-US (language only: units still follow `--units` and the setting). Human files
  written from the web app or by an MCP call follow the Language. Cost: CLI users who pick en-GB get en-US report
  files (docs/settings.md says so).
- **"Kept runs"** in the machine-output rule means runs kept by MCP, sweeps and spreads (under the guard); a run the
  student keeps in the Test tab follows the Language, as it follows Units. Cost: nothing.
- **Sweep and spread tables.** `table.csv`'s rows and grip_watch's loss words stay en-US inside translated page
  tables (machine text). Cost: mixed language in those two tables (a known gap in docs/settings.md).
- **A web-started scenario run's worker** runs inside `i18n.machine()`, so its report.json stays en-US. Cost: session
  and config errors in those runs show in en-US on the run panel (a known gap).
- **summary.json is machine output**: structure and flags only (headline count, uncalibrated, energy_failed,
  title_lines), no words or unit text, byte-identical across language and units. The Bench tab takes the headline
  lines from summary.txt by position. This replaced an earlier summary.json that carried the headlines. Cost: one
  more file per bench run and a positional reader; an old folder without summary.json takes its last warning line as
  the UNCALIBRATED line (old folders only; a rerun fixes it).
- **Runtime warnings built on the simulation's or bridge's thread** (a blocked sensor, a hub the robot never
  opened, a device the robot didn't create) stay raw, allowlisted in the gate: a new thread doesn't inherit the guard,
  so translating there could leak into machine output. Cost: English warnings in en-GB (a known gap).

**The page must not match server text (L3)**

- **Codes instead of words:** ApiError fields (`live`/`already`, `busy/<holder>`), `build_failed`,
  RecordingExists/RecordingTaken, config and scenario errors carrying their fields (validate.py and scenario_validate
  placed them by their English), `ROBOT_FIRST`, a refused save's `place`/`problem` fields, the Bench tab's
  UNCALIBRATED and energy flags, BenchError's `no_entry` code, and station solids carrying their station. Cost:
  nothing.
- **Setup's partner refusal** (blocks/run.py) is found by its PartnerOverlap type through the ConfigError's context,
  and worded from its fields; partner.py's message is wrapped. Cost: nothing.
- **REAL_DS_DRIVING** stays as a constant (four test files check refusals against it), with a comment. Cost: nothing.

**en-US text that changed (each a correction)**

- The HUD mode chip says "Teleop"/"Auto"/"Disabled" (was the raw key), and "1 bodies" became "1 body".
- Gallery and printed forms say `<html lang="en-US">` (was "en"), and the step row's aria-label says "1 check".
- "(1 line in the step)" (was "1 lines") and "no value at 1 tick" (was "1 ticks") in shown text; machine messages
  are unchanged.
- A `[match]` error beside its Scenario-tab field drops the "[match] " prefix (the field is already labeled).

Cost: small deviations from "text unchanged", in shown text only.

**Live changes and gaps**

- **Stored server text is fetched again** on a language change where the page holds it (code panel, Part state,
  library, Drive info). Part tab build messages keep the language they were made in until the next refresh. Cost: a
  mixed-language message until a refresh (rare; a known gap).

**Theme and radii**

- **The light fallback for older browsers.** The `light-dark()` pairs sit inside
  `@supports (color: light-dark(#000, #fff))`, with plain light values outside it. A custom property isn't checked
  when it is read, so "declare the light value first" (the first ruling) couldn't work. Cost: a second copy of the
  light values, pinned by a test; browsers before Chromium 123, Safari 17.5 and Firefox 120 show only the light
  theme (a known gap).
- **The 3D backgrounds** of the Assembler, Drive and Bench views follow the theme, as part3d.js does: a light box in
  a dark page reads as a bug. Cost: a dark background token.
- **The 1px radius** maps to `--radius-sm`, and `PLURAL_RULES` in i18n.py got a comment saying the page uses
  `Intl.PluralRules` (a rule added there must give CLDR's categories). Cost: nothing.

**The British English catalog (Goal 4, L4)**

- **Spellings:** every source with a word from en-GB.spellings.json has an en-GB entry without it
  (test_en_gb_catalog.py). Task 6b added neutralize/neutralized/neutralizing and caliper/calipers to the list. Kept
  out (Task 1): program, license and dialog, since British software text keeps "program" and "dialog", and "license"
  is a verb and a noun.
- **British usage beyond spelling:** "toward" becomes "towards", and "backward" as an adverb ("faces backward")
  "backwards". The rest of the template reads the same in British English: it already says "tick", "round", "all
  round" and 24-hour times, and has no dates, "gotten", "math" or "fall". Cost: nothing; the test holds these two to
  the same rule as the spellings.
- **Words that are keys** in a sentence with a British spelling became placeholders (`center` and `center_in`,
  `toward`, `color`, a bar's `center, axis, length`), so the L4 exceptions list is empty. Cost: five messages
  restructured.
- **The doc-comment examples** ("Color" and "{count} meter|{count} meters" in i18n.py and i18n.js) are not sources,
  so their catalog entries were removed for a clean `scrimmage i18n check en-GB`; the tests use real entries
  ("Center of gravity", cad/checklist.py's aluminum plural). Cost: test edits.

**The gate (L1)**

- **tests/test_i18n_sweep.py** reads the static JS, index.html and every Python module, and fails on an unwrapped
  phrase a student would see (an error or notice in Python; a literal that isn't a key, class, selector or console
  text in JS; text or attributes without data-i18n in HTML). Three modules are whole-file exceptions (`__main__.py`,
  `mcp_server.py`, `i18n_tool.py`: CLI and MCP only). `measure/cli.py` is scanned, because the Measure tab shows
  its field labels and shot_words(); its two CLI-only strings are allowlisted. Every other kept string is an exact
  (file, whole text) entry with its reason, so a new string with the same words is still caught. It found the Scenario tab's input-track panel (trackplot.js), units.js's typed-value
  refusals, a 3D view's lost-context notice and the undo/redo refusals unwrapped, and they were wrapped. Cost: a
  heuristic gate: a lowercase single word or text built from variables can slip past it.

**Cleanup A (2026-11-06)**

- **Sweep and spread tables and kept runs** follow the Language (and Units): see the display-units spec's last
  ruling. This replaces "Sweep and spread tables" and the kept-runs cost above.
- **Cached server text is worded again on a change** where it can be made again cheaply: why a scenario has no robot
  (server.py `_reword_missing`), why a part can't be edited (an import file deleted or with a mistake, a team folder,
  no pieces: part_session `_reword`; a plugin part's files are read again), and a failed fix's result
  (robotcode `_failed`'s `again()`). A build's messages and a CAD file that can't be read keep their words until
  the next build or read: only a build can make them again. Cost: those stay a known gap.
- **Runtime warnings the run's notes show** (a blocked sensor, an unopened hub, both of a valve's channels on, a
  color sensor the robot didn't create) are a `Said`: machine text, worded for people when the report is shown, not
  on the simulation's thread. The bridge's missing-device warnings are only logged and stay American English; the
  run's notes use devicecheck's notes for the same devices. This replaces "Runtime warnings built on the
  simulation's or bridge's thread" above.
