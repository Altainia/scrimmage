"""The words people read, in their language: t() and tn(), the message catalogs, and the Language setting's choices.

    t("Color")                                   -> "Color" (en-US) or "Colour" (en-GB)
    t("Saved {name}", name=part)                 -> named placeholders, so a translation can reorder them
    tn("{count} part", "{count} parts", n)       -> the plural form the language's rules pick; {count} is n
    t("Open", context="file")                    -> for the rare source that means two different things

The source text is American English and is its own key (gettext style), so untranslated text reads as today. A
source must be a literal string, never a variable or text joined from pieces: `scrimmage i18n template` finds the
sources by reading the code (scrimmage/i18n_tool.py). A dynamic part is a {placeholder}; `{{` and `}}` are a literal
brace.

The catalogs are scrimmage/locales/<lang>.json, one flat JSON object per language holding only the entries that
differ from the source:

    {"_language": "English (UK)",                                    the name the Settings menu shows
     "Color": "Colour",
     "{count} meter|{count} meters": {"one": "{count} metre", "other": "{count} metres"},     a plural
     "file\\u0004Open": "..."}                                        a source with a context

Each catalog in the folder is a language (L5); American English is the source and has none. A broken entry (not
text, a placeholder the source doesn't have, a plural form missing) reads as English, with one warning per entry;
a broken catalog file is all English, with one warning (L6). The page's static/i18n.js uses the same catalogs, sent
by catalog_json() in GET /api/settings.

The language is the Language setting (`scrimmage --language` overrides it; settings.current_language()). Machine
text never changes with it: inside units.fixed() and units.machine_text(), which keep machine units, fixed() here
keeps the source's English too (L2).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from . import settings

SOURCE = "en-US"                    # the language the source text is written in
SOURCE_NAME = "English (US)"
LOCALES = Path(__file__).resolve().parent / "locales"
CONTEXT = "\u0004"                  # "<context>\u0004<source>": a source with a context
PLURAL = "|"                        # "<singular>|<plural>": a plural's key
TAG = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")   # en, en-GB, pt-BR, zh-Hant
FIELD = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")
log = logging.getLogger("scrimmage.i18n")

# The plural categories each language uses, and the rule that picks one for a count (CLDR's, for whole and decimal
# counts as Python holds them). A language looks up its own tag, then its base language ("en-GB" -> "en"). One
# without rules uses "other" for every count, with a warning. A contributor adding a language adds its rule here.
PLURAL_RULES: dict[str, tuple[tuple[str, ...], Callable[[float], str]]] = {
    "en": (("one", "other"), lambda n: "one" if n == 1 else "other"),
}

_lock = threading.Lock()
_catalogs: dict[tuple[str, str], dict] = {}          # (folder, tag): its entries
_languages: dict[str, dict[str, str]] = {}           # folder: {tag: name}
_warned: set = set()
_fixed: ContextVar[str | None] = ContextVar("i18n_fixed", default=None)


def reload() -> None:
    """Read the catalogs again (they are read once per process), and warn again about a broken one."""
    with _lock:
        _catalogs.clear()
        _languages.clear()
        _warned.clear()
    settings.forget()       # a setting's language is checked against the languages


def _warn_once(key, message: str) -> None:
    with _lock:
        if key in _warned:
            return
        _warned.add(key)
    log.warning(message)


def read_catalog(path: Path) -> tuple[dict, str | None]:
    """A catalog file's entries, and why it can't be used (then no entries), else None."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        return {}, f"can't read {path} ({e.strerror or e}); its language shows in English"
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return {}, f"{path} isn't valid JSON ({e}); its language shows in English"
    if not isinstance(data, dict):
        return {}, f"{path} should be a JSON object of source text to translation; its language shows in English"
    return data, None


def _folder() -> str:
    return str(LOCALES)


def _catalog(lang: str) -> dict:
    key = (_folder(), lang)
    with _lock:
        if key in _catalogs:
            return _catalogs[key]
    path = LOCALES / f"{lang}.json"
    if lang == SOURCE:
        found = {}
    elif not path.is_file():
        found = {}
        _warn_once(("no catalog", key), f"there's no {path.name} in {LOCALES}; {lang} shows in English")
    else:
        found, problem = read_catalog(path)
        if problem:
            _warn_once(("catalog", key), problem)
    with _lock:
        _catalogs[key] = found
    return found


def languages() -> dict[str, str]:
    """{tag: name}: American English (the source), then each catalog in the locales folder by tag. A catalog names
    itself with "_language"; one that doesn't (or can't be read) is named by its tag."""
    folder = _folder()
    with _lock:
        if folder in _languages:
            return dict(_languages[folder])
    found = {SOURCE: SOURCE_NAME}
    for path in sorted(LOCALES.glob("*.json")) if LOCALES.is_dir() else []:
        tag = path.stem
        if "." in tag or not TAG.match(tag) or tag == SOURCE:     # en-GB.spellings.json is a helper, not a language
            continue
        name = _catalog(tag).get("_language")
        found[tag] = name if isinstance(name, str) and name.strip() else tag
    with _lock:
        _languages[folder] = found
    return dict(found)


@contextmanager
def fixed(lang: str = SOURCE):
    """Write in `lang` inside the `with` (this thread or task only), whatever the setting. units.fixed() and
    units.machine_text() enter it with the source's English, so machine text keeps its words."""
    token = _fixed.set(lang)
    try:
        yield
    finally:
        _fixed.reset(token)


def current_language() -> str:
    """The language text is written in: a `fixed()` one, else `--language`, else the Language setting."""
    return _fixed.get() or settings.current_language()


def _rule(lang: str):
    return PLURAL_RULES.get(lang) or PLURAL_RULES.get(lang.split("-")[0])


def plural_categories(lang: str) -> tuple[str, ...]:
    """The plural forms a catalog's plural entry must have for `lang` (just "other" for a language without rules)."""
    rule = _rule(lang)
    return rule[0] if rule else ("other",)


def plural_category(lang: str, n: float) -> str:
    """The CLDR plural category of `n` in `lang`: "one" or "other" in English."""
    rule = _rule(lang)
    if rule is None:
        _warn_once(("rules", lang), f"there are no plural rules for {lang} (scrimmage/i18n.py PLURAL_RULES); "
                                    f"its plurals use their \"other\" form")
        return "other"
    return rule[1](n)


def names(text: str) -> set[str]:
    """The {placeholders} in `text`."""
    return {m.group(1) for m in FIELD.finditer(text) if m.group(1)}


def _key(source: str, context: str | None) -> str:
    return f"{context}{CONTEXT}{source}" if context else source


def _split(key: str) -> tuple[str | None, str]:
    context, sep, source = key.rpartition(CONTEXT)
    return (context if sep else None), source


def entry_problem(key: str, entry, lang: str, plural: bool) -> str | None:
    """Why a catalog entry can't be used (it then reads as English), else None. `plural`: the key is tn()'s. An
    empty translation ("", or a plural whose forms are all "") is untranslated, not broken."""
    _, source = _split(key)
    forms = plural_categories(lang)
    if not plural:
        if not isinstance(entry, str):
            return "should be text"
        extra = names(entry) - names(source)
        return f"uses {_listed(extra)}, which the source doesn't have" if extra else None
    if not isinstance(entry, dict):
        return f"should be a plural's forms, an object of {' and '.join(forms)}"
    if all(v == "" for v in entry.values()):
        return None
    singular, _, plural_text = source.partition(PLURAL)
    allowed = names(singular) | names(plural_text) | {"count"}
    for form in forms:
        if not isinstance(entry.get(form), str) or not entry[form]:
            return f"has no {form} form (a plural in {lang} needs {' and '.join(forms)})"
    for form, text in entry.items():
        if not isinstance(text, str):
            return f"{form} should be text"
        extra = names(text) - allowed
        if extra:
            return f"{form} uses {_listed(extra)}, which the source doesn't have"
    return None


def _listed(fields: set[str]) -> str:
    return ", ".join("{" + n + "}" for n in sorted(fields))


def _entry(key: str, lang: str, plural: bool):
    """The usable catalog entry for `key` in `lang`, else None (untranslated, or broken: one warning)."""
    if lang == SOURCE:
        return None
    entry = _catalog(lang).get(key)
    if entry is None or entry == "" or (isinstance(entry, dict) and all(v == "" for v in entry.values())):
        return None
    problem = entry_problem(key, entry, lang, plural)
    if problem:
        _warn_once(("entry", _folder(), lang, key), f"{lang}.json: the entry {key!r} {problem}; it shows in English")
        return None
    return entry


def _fill(text: str, params: dict, source: str) -> str:
    def one(m: re.Match) -> str:
        if m.group(0) == "{{":
            return "{"
        if m.group(0) == "}}":
            return "}"
        if m.group(1) not in params:
            raise KeyError(f"{source!r} needs a value for {{{m.group(1)}}}")
        return str(params[m.group(1)])
    return FIELD.sub(one, text)


def t(source: str, /, *, lang: str | None = None, context: str | None = None, **params) -> str:
    """`source` (American English, a literal) in `lang` (default: current_language()), its placeholders filled."""
    entry = _entry(_key(source, context), lang or current_language(), plural=False)
    return _fill(entry or source, params, source)


def tn(singular: str, plural: str, count: float, /, *, lang: str | None = None, context: str | None = None,
       **params) -> str:
    """The form of a plural for `count` in `lang`; {count} is `count`. Untranslated, English's rule picks
    `singular` (one) or `plural` (other)."""
    lang = lang or current_language()
    params = {"count": count, **params}
    entry = _entry(_key(f"{singular}{PLURAL}{plural}", context), lang, plural=True)
    if entry:
        text = entry[plural_category(lang, count)]
    else:
        text = singular if plural_category(SOURCE, count) == "one" else plural
    return _fill(text, params, singular)


def catalog_json(lang: str) -> dict:
    """The page's catalog for `lang` (static/i18n.js): its entries as the file has them, without "_language"; it
    checks each entry as it is used, as t() does."""
    if lang == SOURCE:
        return {}
    return {k: v for k, v in _catalog(lang).items() if not k.startswith("_")}
