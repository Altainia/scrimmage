"""The British English catalog (scrimmage/locales/en-GB.json, spec Goal 4 and L4).

- **Complete for the spelling list (L4):** every source `scrimmage i18n template` finds that holds an American word
  from en-GB.spellings.json (whole word, any case, outside its {placeholders}) has an en-GB entry, and the entry
  doesn't keep the word. USAGE adds the British usage chosen beyond spelling (docs: the spec's decisions).
- **Clean for the tool:** `scrimmage i18n check en-GB` finds nothing broken, no placeholder mismatch and no unused
  entry.
- **Nothing a student types or the code fills is translated:** an entry has exactly its source's placeholders, and
  keeps every quoted value, [table] and key = value its source has, as written (the 6d ruling).
"""
import json
import re
from pathlib import Path

from i18n_helpers import tokens as _tokens          # the typed-values gate's token finder
from scrimmage import i18n, i18n_tool

LOCALES = Path(i18n.__file__).resolve().parent / "locales"
SPELLINGS = {k: v for k, v in json.loads((LOCALES / "en-GB.spellings.json").read_text(encoding="utf-8")).items()
             if not k.startswith("_")}
# British usage beyond spelling, held to the same rule: "towards" and "backwards" (as adverbs) are the British forms
# "backward" -> "backwards" assumes the adverb ("faces backward"); an adjective ("a backward step") stays "backward"
# in British English too, so a source using it so needs an EXCEPTIONS entry.
USAGE = {"toward": "towards", "backward": "backwards"}
WORDS = {**SPELLINGS, **USAGE}
AMERICAN = re.compile(r"\b(" + "|".join(map(re.escape, sorted(WORDS, key=len, reverse=True))) + r")\b", re.I)
FIELD = re.compile(r"\{\{|\}\}|\{[A-Za-z_]\w*\}")

# (source, American word) -> why the entry may keep the word: a word that is code inside a sentence (L4's
# exceptions). Empty: the 6d ruling made each such word a {placeholder} instead.
EXCEPTIONS: dict[tuple[str, str], str] = {}


def _words(text: str) -> str:
    return FIELD.sub(" ", text)


def _catalog() -> dict:
    return json.loads((LOCALES / "en-GB.json").read_text(encoding="utf-8"))


def _sources():
    sources, errors = i18n_tool.scan()
    assert not errors, errors
    return sources


def _forms(source: str, plural: bool, entry) -> list[tuple[str, str]]:
    """(source text, its translation) pairs: one, or one per plural form."""
    if not plural:
        return [(source, entry)]
    one, other = source.split(i18n.PLURAL)
    return [(one, entry["one"]), (other, entry["other"])]


def test_every_source_with_an_american_spelling_has_a_british_entry():
    catalog = _catalog()
    missing, kept = [], []
    for s in _sources():
        source = s.key.split(i18n.CONTEXT)[-1]
        if not AMERICAN.search(_words(source)):
            continue
        if s.key not in catalog:
            missing.append(f"{s.where[0]}: {source!r}")
            continue
        for _, text in _forms(source, s.plural, catalog[s.key]):
            for m in AMERICAN.finditer(_words(text)):
                if (source, m.group(1).lower()) not in EXCEPTIONS:
                    kept.append(f"{s.where[0]}: {m.group(1)!r} in {text!r}")
    assert not missing, "add en-GB entries:\n" + "\n".join(missing)
    assert not kept, "these entries keep the American word:\n" + "\n".join(kept)


def test_the_entries_keep_what_is_typed_or_filled_in():
    """An entry's placeholders, quoted values, [tables] and settings are its source's, exactly."""
    by_key = {s.key: s for s in _sources()}
    for key, entry in _catalog().items():
        if key.startswith("_"):
            continue
        s = by_key[key]
        for source, text in _forms(key.split(i18n.CONTEXT)[-1], s.plural, entry):
            assert sorted(FIELD.findall(text)) == sorted(FIELD.findall(source)), key
            assert _tokens(text) == _tokens(source), key


def test_the_tool_finds_nothing_wrong_with_it(capsys):
    report = i18n_tool.check("en-GB", _sources())
    assert report.name == "English (UK)"
    assert not report.broken and not report.mismatched and not report.unused, report
    assert report.entries == len(_catalog()) - 1
    args = type("Args", (), {"i18n_command": "check", "lang": "en-GB", "root": None, "locales": None})()
    assert i18n_tool.main(args) == 0
    out = capsys.readouterr().out
    titles = [line.strip() for line in out.splitlines() if line.startswith("  ") and not line.startswith("    ")]
    assert [x.split(":")[0] for x in titles] == ["missing (shown in American English)"], titles


def test_every_exception_is_still_needed():
    catalog = _catalog()
    for (source, word), why in EXCEPTIONS.items():
        assert why and source in catalog and word in _words(catalog[source]).lower()
