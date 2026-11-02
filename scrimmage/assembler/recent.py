"""The start page's recent list: robots, scenarios, idea sessions and parts from CAD opened in the editor, newest
first. Each entry's kind is "robot", "scenario", "session" (a folder) or "import" (an import file, or a plugin-made
part folder).

Kept in `$SCRIMMAGE_CACHE/recent.json`, else `~/.cache/scrimmage/recent.json` (`$SCRIMMAGE_RECENT`, a file path, overrides
both: the tests use it so they never touch the user's list). A file or folder that no longer exists is
left out when the list is read. The list is a convenience: a store that can't be read or written never stops the
editor.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

MAX = 12


def _store() -> Path:
    if os.environ.get("SCRIMMAGE_RECENT"):
        return Path(os.environ["SCRIMMAGE_RECENT"])
    base = os.environ.get("SCRIMMAGE_CACHE")
    return (Path(base) if base else Path.home() / ".cache" / "scrimmage") / "recent.json"


def _read() -> list[dict]:
    try:
        data = json.loads(_store().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict) and isinstance(e.get("path"), str)
            and isinstance(e.get("kind"), str)] if isinstance(data, list) else []


def remember(path: Path, kind: str) -> None:
    """Put `path` (a robot, scenario, session folder, import file or part folder) at the top of the list."""
    p = str(Path(path).resolve())
    entries = [{"path": p, "kind": kind, "t": time.time()}, *(e for e in _read() if e["path"] != p)][:MAX]
    store = _store()
    tmp = None
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=store.parent, prefix="recent.", suffix=".tmp", delete=False,
                                         encoding="utf-8") as f:
            tmp = f.name
            json.dump(entries, f, indent=1)
        os.replace(tmp, store)
    except OSError:
        if tmp is not None:
            Path(tmp).unlink(missing_ok=True)


def recent() -> list[dict]:
    """[{path, kind, t}], newest first, at most MAX, leaving out paths that no longer exist."""
    return [e for e in _read() if Path(e["path"]).exists()][:MAX]
