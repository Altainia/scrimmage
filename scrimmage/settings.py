"""The user's settings, kept per computer so every tab, every browser and the CLI agree: Units, Language and Theme.

    units = "imperial"     # or "metric": how the web app and server-built text show values (scrimmage/units.py)
    language = "en-US"     # or "en-GB", or another catalog's tag: the language they are written in (scrimmage/i18n.py)
    theme = "system"       # or "light" or "dark": the web app's colors (System follows the computer's dark mode)

The file is `<config dir>/scrimmage/settings.toml`: `$XDG_CONFIG_HOME` (else `~/.config`) on Linux, `%APPDATA%` on
Windows, `~/Library/Application Support` on macOS. `$SCRIMMAGE_CONFIG` names the folder instead (the tests use it).
A missing file is the defaults. A file that can't be read, isn't TOML or has a bad value is the defaults too, with
one warning, never an error: settings are a convenience (a bad value is the default for that setting only). load()
reads the file again when it changes on disk, so an edit from another editor, the CLI or by hand applies at once.
`scrimmage --units` and `--language` override the file for one run (set_override, set_language_override);
current_units() and current_language() are what every display uses.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import shutil
import sys
import tempfile
import threading
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError

SYSTEMS = ("imperial", "metric")
THEMES = ("system", "light", "dark")
FILE = "settings.toml"
log = logging.getLogger("scrimmage.settings")


@dataclass(frozen=True)
class Settings:
    units: str = "imperial"   # FRC is imperial
    language: str = "en-US"   # the source text's own (i18n.SOURCE)
    theme: str = "system"     # the computer's light or dark mode, as before there was a choice


DEFAULTS = "imperial units, American English and the System theme"
_lock = threading.Lock()
# (the file's stat, what it says, its problems: each its American English text and a function wording it for people)
_cache: tuple[tuple, Settings, list] | None = None
_override: str | None = None
_language_override: str | None = None


def settings_dir() -> Path:
    if os.environ.get("SCRIMMAGE_CONFIG"):
        return Path(os.environ["SCRIMMAGE_CONFIG"])
    if sys.platform == "win32":
        base = Path(os.environ["APPDATA"]) if os.environ.get("APPDATA") else Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or ""
        base = Path(xdg) if os.path.isabs(xdg) else Path.home() / ".config"
    return base / "scrimmage"


def settings_path() -> Path:
    return settings_dir() / FILE


def check_units(value) -> str:
    if not isinstance(value, str) or value.strip().lower() not in SYSTEMS:
        shown = f'"{value}"' if isinstance(value, str) else repr(value)
        raise ValueError(f'units must be "imperial" or "metric", not {shown}')
    return value.strip().lower()


def check_theme(value) -> str:
    if not isinstance(value, str) or value.strip().lower() not in THEMES:
        raise ValueError(f'theme must be "system", "light" or "dark", not {_shown(value)}')
    return value.strip().lower()


def check_language(value) -> str:
    """A language tag Scrimmage has a catalog for (i18n.languages()), as that list spells it ("en-gb" is en-GB)."""
    from .i18n import languages     # i18n reads the settings: imported here, when first needed
    known = languages()
    if isinstance(value, str):
        for tag in known:
            if tag.lower() == value.strip().lower():
                return tag
    raise ValueError(f"language must be one of {', '.join(_shown(t) for t in known)}, not {_shown(value)}")


def _shown(value) -> str:
    return f'"{value}"' if isinstance(value, str) else repr(value)


def _i18n():
    from . import i18n      # i18n reads the settings: imported when a problem is worded, after the file is read
    return i18n


def _read(path: Path) -> tuple[Settings, list[tuple[str, object]]]:
    """The file's settings and its problems, each (its American English text, for the log, and a function wording it
    in the Language setting when it is shown: the problems are found before the language is known)."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Settings(), []
    except OSError as e:
        why = e.strerror or e
        return Settings(), [(f"can't read {path} ({why}); using {DEFAULTS}",
                             lambda: _i18n().t("can't read {path} ({why}); using imperial units, American English "
                                               "and the System theme", path=path, why=why))]
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        why = str(e)   # (e is gone after the except: the words are made later)
        return Settings(), [(f"{path} isn't valid TOML ({e}); using {DEFAULTS} (changing a setting replaces it, keeping a copy as {path.name}.bad)",
                             lambda: _i18n().t("{path} isn't valid TOML ({why}); using imperial units, American "
                                               "English and the System theme (changing a setting replaces it, "
                                               "keeping a copy as {copy})", path=path, why=why,
                                               copy=f"{path.name}.bad"))]
    found, problems = {}, []
    if "units" in data:
        try:
            found["units"] = check_units(data["units"])
        except ValueError:
            value = _shown(data["units"])
            problems.append((f"{path}: units = {value} isn't imperial or metric; using imperial units",
                             lambda value=value: _i18n().t("{path}: units = {value} isn't imperial or metric; "
                                                           "using imperial units", path=path, value=value)))
    if "language" in data:
        try:
            found["language"] = check_language(data["language"])
        except ValueError:
            from .i18n import languages
            value, known = _shown(data["language"]), ", ".join(languages())
            problems.append((f"{path}: language = {value} isn't one of Scrimmage's languages "
                             f"({known}); using American English (en-US)",
                             lambda value=value: _i18n().t("{path}: language = {value} isn't one of Scrimmage's "
                                                           "languages ({languages}); using American English (en-US)",
                                                           path=path, value=value, languages=known)))
    if "theme" in data:
        try:
            found["theme"] = check_theme(data["theme"])
        except ValueError:
            value = _shown(data["theme"])
            problems.append((f"{path}: theme = {value} isn't system, light or dark; "
                             "using the System theme",
                             lambda value=value: _i18n().t("{path}: theme = {value} isn't system, light or dark; "
                                                           "using the System theme", path=path, value=value)))
    return Settings(**found), problems


def _stat(path: Path) -> tuple:
    try:
        st = path.stat()
    except OSError:
        return (str(path), None)
    return (str(path), st.st_ino, st.st_mtime_ns, st.st_size)


def _current() -> tuple[Settings, list]:
    global _cache
    path = settings_path()
    key = _stat(path)
    with _lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1], _cache[2]
        found, problems = _read(path)
        if problems:
            log.warning("; ".join(text for text, _ in problems))   # once per state of the file: the next read is cached
        _cache = (key, found, problems)
        return found, problems


def load() -> Settings:
    """The settings file's settings: the defaults for anything it doesn't say, or can't (see problem())."""
    return _current()[0]


def problem() -> str | None:
    """Why the settings file is being ignored (it can't be read, isn't TOML, has a bad value), else None: in
    American English, as the log has it."""
    return "; ".join(text for text, _ in _current()[1]) or None


def problem_words() -> str | None:
    """problem() in the Language setting (the web app's warning): worded when asked, once the language is known."""
    return "; ".join(words() for _, words in _current()[1]) or None


def save(**changes) -> None:
    """Change settings in the file, keeping its comments and any settings this version doesn't know. The file is
    replaced in one step (a temporary file, then a rename), so a reader never sees half of it."""
    fields = {f.name for f in dataclasses.fields(Settings)}
    for name in changes:
        if name not in fields:
            raise ValueError(f"unknown setting {name!r}; the settings are {', '.join(sorted(fields))}")
    if "units" in changes:
        changes["units"] = check_units(changes["units"])
    if "language" in changes:
        changes["language"] = check_language(changes["language"])
    if "theme" in changes:
        changes["theme"] = check_theme(changes["theme"])
    global _cache
    path = settings_path()
    with _lock:
        try:
            doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, TOMLKitError):   # missing or broken: start again
            doc = tomlkit.document()
            if path.is_file():      # not valid TOML: keep it beside the new one
                bad = path.with_name(path.name + ".bad")
                try:
                    shutil.copyfile(path, bad)
                    log.warning("%s isn't valid TOML; it was replaced, and the old file kept as %s", path, bad)
                except OSError:
                    pass
        for name, value in changes.items():
            doc[name] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix="settings.", suffix=".tmp", delete=False,
                                             encoding="utf-8") as f:
                tmp = f.name
                f.write(tomlkit.dumps(doc))
            os.replace(tmp, path)
        except OSError:
            if tmp is not None:
                Path(tmp).unlink(missing_ok=True)
            raise
        finally:
            _cache = None


def set_override(units: str | None) -> None:
    """`scrimmage --units`: this process shows `units` whatever the file says (None: the file again)."""
    global _override
    _override = None if units is None else check_units(units)


def overridden() -> bool:
    return _override is not None


def current_units() -> str:
    """The unit system to show values in: `--units` if given, else the settings file's."""
    return _override or load().units


def set_language_override(language: str | None) -> None:
    """`scrimmage --language`: this process writes in `language` whatever the file says (None: the file again)."""
    global _language_override
    _language_override = None if language is None else check_language(language)


def language_overridden() -> bool:
    return _language_override is not None


def current_language() -> str:
    """The language to write in: `--language` if given, else the settings file's. Text built inside a machine guard
    keeps American English whatever this says: i18n.current_language() is what the translation calls use."""
    return _language_override or load().language


def forget() -> None:
    """Read the file again next time (the languages it is checked against changed: i18n.reload())."""
    global _cache
    with _lock:
        _cache = None
