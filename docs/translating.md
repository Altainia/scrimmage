# Translating Scrimmage

The web app (`scrimmage edit`) can show its words in another language. Today it has **American English** (en-US,
the words in the code) and **British English** (en-GB). This guide is for anyone who wants to add a language or fix
a translation. You don't need to know Python or JavaScript to translate: a language is one JSON file.

Each person picks a language in the editor's **Settings** menu (see [settings.md](settings.md#language)).

Contents: [What gets translated](#what-gets-translated), [Add a language](#add-a-language),
[How an entry is written](#how-an-entry-is-written), [Plurals](#plurals), [Contexts](#contexts),
[Never translate these](#never-translate-these), ["(s)" plurals](#s-plurals),
[Check your file](#check-your-file), [Try it in the web app](#try-it-in-the-web-app),
[For code contributors](#for-code-contributors).

## What gets translated

Everything a student reads in the web app:

- the page itself: every tab, button, label, tooltip, the Test tab's blocks and their tooltips, canvas drawings,
  notices and errors;
- the text the server sends the page: problems in a file, run outcomes and check lines, Bench headlines, Measure
  text;
- the files written for people from the web app: `report.md`, a bench run's `summary.txt`, chart labels.

These stay in American English for now:

- the docs (translations of them are welcome later, as separate files);
- the command line: what `scrimmage run`, `test`, `bench` and the other commands print, their `--help`, and the
  report files they write;
- everything a script or an agent reads by machine (see [Never translate these](#never-translate-these)).

## Add a language

1. **Make the file.** Pick the language's tag: a two- or three-letter code, with a region if you need one
   (`fr`, `es-MX`, `pt-BR`). Then, from the repo folder:

   ```bash
   scrimmage i18n template --out scrimmage/locales/fr.json
   ```

   This writes every piece of text the code wraps (over 4,000 of them), each with an empty translation. Without
   `--out`, `scrimmage i18n template` just lists them, each with the `file:line` that uses it, which helps when you
   want to see where a piece of text appears.

2. **Name it.** Set `_language` at the top to the language's own name, as its speakers write it. That is what the
   Settings menu shows:

   ```json
   {
     "_language": "Français",
     "Settings": "Réglages",
     "Undo": "Annuler"
   }
   ```

3. **Translate.** Fill in the entries you can. An entry left `""` isn't translated yet: the page shows the American
   English text, and nothing breaks. You can also delete entries you won't translate. A file only needs the entries
   that are different from American English. That is why `en-GB.json` has fewer than 100 entries.

4. **Check it** with `scrimmage i18n check fr` ([below](#check-your-file)), and **try it** in the web app.

Adding a language is just that file. Scrimmage finds every `<tag>.json` in `scrimmage/locales/`, and the Settings
menu and `--language` list it. (`en-GB.spellings.json` lives there too. It is a word list for a test, not a
language.)

If your language's plurals aren't like English's, add its plural rule too ([Plurals](#plurals)).

## How an entry is written

The key is the American English text, exactly as the code has it. The value is your translation:

```json
"Couldn't save the file": "Impossible d'enregistrer le fichier"
```

**Placeholders.** A word in braces, such as `{name}` or `{count}`, is filled in when the text is shown: a part's
name, a number, a file's path. Keep every placeholder, spelled exactly the same. You can move it to wherever your
language needs it:

```json
"{who} moved {what}": "{what} déplacé par {who}"
```

The placeholder's name tells you what goes there: `{path}` is a file, `{count}` a number, `{problem}` another
message. When a name ends in `_setting`, `_table` or `_form` (`{kind_setting}`, `{range_table}`), it is something a
student types into a file, such as `kind = "disc"` or `[range]`. Keep it as a placeholder; never write its words
out.

**Literal braces.** `{{` and `}}` show as a single `{` and `}`. Keep them doubled in your translation.

**Mistakes don't break anything.** An entry with a wrong placeholder, or a plural missing a form, shows the American
English text instead. The page warns once about it in the browser's console (F12), the server writes one warning in
its log, and `scrimmage i18n check` lists it.

## Plurals

Some text changes with a number: "1 part", "2 parts". Its key is the two English forms joined with `|`, and its
value has one entry for each plural form your language uses:

```json
"{count} part|{count} parts": {"one": "{count} pièce", "other": "{count} pièces"}
```

The form names come from the Unicode CLDR plural rules: `zero`, `one`, `two`, `few`, `many` and `other`. English
and French use `one` and `other`. Polish uses `one`, `few`, `many` and `other`. Give every form your language has.
CLDR's tables list them: <https://www.unicode.org/cldr/charts/latest/supplemental/language_plural_rules.html>.

Who picks the form:

- **The page** uses the browser's own CLDR rules (`Intl.PluralRules`), so it already knows every language.
- **The server** uses the small table `PLURAL_RULES` in `scrimmage/i18n.py`. It has English now. Add your language's
  rule there (its forms, and a function that picks one for a number), giving the same forms CLDR gives. A language
  without a rule uses `other` for every number, with a warning.

In the code, these come from `tn()` (`tn("{count} part", "{count} parts", n)`), where `{count}` is the number.

## Contexts

The same English words can mean two different things. For those, the code adds a context, and the key is the
context, the character `\u0004`, then the text:

```json
"theme\u0004System": "Système",
"list separator\u0004 and ": " et "
```

The contexts in use:

- **`list separator`**: the words between the items of a list, such as `" and "` and `" or "` in "a, b and c". They
  have their spaces in them; keep spaces your language needs.
- `theme`: the Theme choices (System, Light, Dark) in the Settings menu.
- `verb` and `noun`: a word that can be either ("Frame" and "Step" as things to do, "Run" as a thing).
- `blocks`, `field`, `page`, `partner`, `pose`, `range`, `release box`, `zone`: a word that means something special in that part of the
  app (the Test tab's "Move" and "Wait" blocks, the start page's "Start").

`scrimmage i18n template` shows each one's key, with its context, and where it is used.

## Never translate these

Some text looks like words but is something the code or a student relies on exactly as written. It is never in
the template, or only as a `{placeholder}`, so you won't see it, but it's good to know why:

- **File keys and values a student types**, such as `color`, `center`, `kind = "disc"`, `[shooter]` and
  `pip install "scrimmage[cad]"`. A student copies them into a file, and the file must say them in English. A
  British "colour" there would make the file wrong. In a sentence, these are always placeholders.
- **Status keys**, such as `"cancelled"`, `"timed out"` and `"check failed"`. The code compares them. The page shows
  them through their own entries.
- **Machine output:** MCP tool results, `report.json`, the CSV and JSON files (a sweep's `table.csv`, a bench run's
  `pieces.csv`, `shafts.csv`, `loads.csv`, `landings.csv` and `summary.json`, a bench sweep's `results.csv`), and what the command line prints. Scripts and agents read these, so they stay
  American English whatever the setting. The code makes sure of it, so you can translate every entry you see.

## "(s)" plurals

A few messages about a file write a plural as "(s)": "has {tables} table(s)", "missing required key(s):
{missing}". They aren't `tn()` plurals, so they have only one form. Translate them whichever way reads best in your
language. You can rewrite the sentence so it doesn't need a plural ("tables: {tables}"), as long as every
placeholder stays.

## Check your file

```bash
scrimmage i18n check fr
```

It reads the code and your file, and lists:

- **broken**: an entry that can't be used (shown in American English), and why;
- **placeholders don't match**: a translation that leaves out a placeholder its text has;
- **unused**: an entry whose English text is no longer in the code (the text changed, or went away). Delete it,
  or move your translation to the new text;
- **missing**: text your file doesn't translate yet. That's fine: it shows in American English.

It exits with 1 when an entry is broken or doesn't match, so you can run it before you send your file. Without a
tag, `scrimmage i18n check` checks every language.

## Try it in the web app

1. Start the editor again after you change the file: Scrimmage reads the language files once, when it starts.

   ```bash
   scrimmage --language fr edit configs/kitbot.toml
   ```

   `--language fr` picks your language for that run. You can also pick it in the **Settings** menu, which saves it
   for next time.

2. Go through the tabs. A broken entry shows in English, with a warning in the browser's console (F12).

3. Change the language in the Settings menu: the page switches at once, without a reload, and keeps what you were
   typing.

## For code contributors

When you write text a student will read:

- **Wrap it** with `t("...")` (Python: `from .i18n import t`; JavaScript: `import { t } from "./i18n.js"`), or
  `tn(singular, plural, count)` for a plural. The text must be one literal string, the whole sentence, so the
  template can find it and a translator sees all of it. Put the parts that change in `{placeholders}`: never join
  text with `+`, an f-string or `${...}`.
- **Text defined when a module loads** (a table of part docs, a constant) is marked with `N_("...")`, and shown with
  `translated(...)` at the time it is shown. A `t()` at import time would freeze the language the process started
  in.
- **index.html** names its text in `data-i18n="..."` (and `data-i18n-title`, `data-i18n-placeholder`,
  `data-i18n-aria-label`, with `data-i18n-context` for a context).
- **Use a context** (`t("Open", context="file")`) only when the same words need two different translations.
- **What a student types** (a key, a value, a `[table]`, a command) goes in a placeholder, never in the words.
- **Machine output** keeps American English inside a guard. An MCP tool runs inside `i18n.machine()`: what it
  returns is American English, while the files it writes for people follow the Language. So register a new MCP
  tool with `@_tool()` in `mcp_server.py`, which enters the guard, and run its blocking work through `_in_thread()`,
  which enters it again in the worker thread (a thread doesn't inherit it). A scenario run
  started from the web app writes `report.json` inside `i18n.machine()` too. The Test tab's `table.csv` and MCP
  `test_run` are made inside `units.machine_text()`, which also keeps the units' old words. A run made there keeps
  its words in every language and unit system too (`units.each_setting`, blocks/run_words.py's `_w`), so the Test tab
  shows it in the viewer's: word such text with t() while the run's numbers are at hand. A warning made on the
  simulation's thread is a `Said` (checks.py) whose words are made when it is shown, never on that thread. `i18n.fixed()` is the
  CLI's guard: everything, files for people too, in American English.
- **The page must not find server text by its words.** Send a code or a field, and branch on that.

The tests that keep this true:

- `tests/test_i18n_sweep.py` fails on text a student would see that isn't wrapped. If a string must stay as it is,
  add it to its `ALLOWED` list, with the reason.
- `tests/test_i18n_typed.py` fails on a typed key, value or table inside translatable text.
- `tests/test_i18n_calls.py` checks that each call gives exactly the placeholders its text has.
- `tests/test_i18n_import_time.py` fails on a `t()` that runs when a module loads.
- `tests/test_en_gb_catalog.py` keeps British English complete for the spellings in `en-GB.spellings.json`. Text
  with "color", "center", "meter" or "aluminum" needs an en-GB entry with the British spelling.
- `tests/test_mcp_language.py` and `tests/test_units_machine_text.py` check that machine output is the same in every
  language.
