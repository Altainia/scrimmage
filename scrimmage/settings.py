"""The user's settings, kept per computer so every tab, every browser and the CLI agree: today only Units.

    units = "imperial"     # or "metric": how the web app and server-built text show values (scrimmage/units.py)

The file is `<config dir>/scrimmage/settings.toml`: `$XDG_CONFIG_HOME` (else `~/.config`) on Linux, `%APPDATA%` on
Windows, `~/Library/Application Support` on macOS. `$SCRIMMAGE_CONFIG` names the folder instead (the tests use it).
A missing file is the defaults. A file that can't be read, isn't TOML or has a bad value is the defaults too, with
one warning, never an error: settings are a convenience. load() reads the file again when it changes on disk, so an
edit from another editor, the CLI or by hand applies at once. `scrimmage --units` overrides the file for one run
(set_override); current_units() is what every display uses.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
import tempfile
import threading
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError

SYSTEMS = ("imperial", "metric")
FILE = "settings.toml"
log = logging.getLogger("scrimmage.settings")


@dataclass(frozen=True)
class Settings:
    units: str = "imperial"   # FRC is imperial


_lock = threading.Lock()
_cache: tuple[tuple, Settings, str | None] | None = None   # (the file's stat, what it says, its problem)
_override: str | None = None


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


def _read(path: Path) -> tuple[Settings, str | None]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Settings(), None
    except OSError as e:
        return Settings(), f"can't read {path} ({e.strerror or e}); using imperial units"
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return Settings(), f"{path} isn't valid TOML ({e}); using imperial units"
    if "units" not in data:
        return Settings(), None
    try:
        return Settings(units=check_units(data["units"])), None
    except ValueError:
        shown = f'"{data["units"]}"' if isinstance(data["units"], str) else repr(data["units"])
        return Settings(), f"{path}: units = {shown} isn't imperial or metric; using imperial units"


def _stat(path: Path) -> tuple:
    try:
        st = path.stat()
    except OSError:
        return (str(path), None)
    return (str(path), st.st_ino, st.st_mtime_ns, st.st_size)


def _current() -> tuple[Settings, str | None]:
    global _cache
    path = settings_path()
    key = _stat(path)
    with _lock:
        if _cache is not None and _cache[0] == key:
            return _cache[1], _cache[2]
        found, problem = _read(path)
        if problem:
            log.warning(problem)   # once per state of the file: the next read is the cached one
        _cache = (key, found, problem)
        return found, problem


def load() -> Settings:
    """The settings file's settings: the defaults for anything it doesn't say, or can't (see problem())."""
    return _current()[0]


def problem() -> str | None:
    """Why the settings file is being ignored (it can't be read, isn't TOML, has a bad value), else None."""
    return _current()[1]


def save(**changes) -> None:
    """Change settings in the file, keeping its comments and any settings this version doesn't know. The file is
    replaced in one step (a temporary file, then a rename), so a reader never sees half of it."""
    fields = {f.name for f in dataclasses.fields(Settings)}
    for name in changes:
        if name not in fields:
            raise ValueError(f"unknown setting {name!r}; the settings are {', '.join(sorted(fields))}")
    if "units" in changes:
        changes["units"] = check_units(changes["units"])
    global _cache
    path = settings_path()
    with _lock:
        try:
            doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, TOMLKitError):   # missing or broken: start again
            doc = tomlkit.document()
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
