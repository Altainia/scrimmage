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
