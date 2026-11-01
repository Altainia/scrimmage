"""The team's shared folders, set per computer: `{cad}` (CAD and parts) and `{code}` (robot code).

A robot file can say `{cad}/arm` or `{code}/5098-2026` instead of one laptop's path. Each computer says where those
folders are, in `$SCRIMMAGE_CACHE/folders.json` (else `~/.cache/scrimmage/folders.json`, beside the start page's
recent list); `$SCRIMMAGE_FOLDERS`, a file path, overrides it (the tests use that). The environment variables
`SCRIMMAGE_CAD` and `SCRIMMAGE_CODE` win over the file.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

FOLDERS = ("cad", "code")
_LABEL = {"cad": "CAD", "code": "code"}
_PLACEHOLDER = re.compile(r"\{(cad|code)\}")


class TeamFolderError(ValueError):
    pass


def _store() -> Path:
    if os.environ.get("SCRIMMAGE_FOLDERS"):
        return Path(os.environ["SCRIMMAGE_FOLDERS"])
    base = os.environ.get("SCRIMMAGE_CACHE")
    return (Path(base) if base else Path.home() / ".cache" / "scrimmage") / "folders.json"


def _read() -> dict:
    try:
        data = json.loads(_store().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def folders() -> dict[str, Path | None]:
    """Each team folder on this computer (None when not set). The environment variables win over the file."""
    saved = _read()
    out: dict[str, Path | None] = {}
    for name in FOLDERS:
        value = os.environ.get(f"SCRIMMAGE_{name.upper()}") or saved.get(name)
        out[name] = Path(value) if isinstance(value, str) and value else None
    return out


def set_folder(name: str, path: Path | None) -> None:
    """Remember (or, with None, forget) a team folder on this computer. The file is replaced in one step."""
    if name not in FOLDERS:
        raise ValueError(f"unknown team folder {name!r}; the team folders are {', '.join(FOLDERS)}")
    data = {k: v for k, v in _read().items() if isinstance(v, str)}
    if path is None:
        data.pop(name, None)
    else:
        data[name] = str(path)
    store = _store()
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=store.parent, prefix="folders.", suffix=".tmp", delete=False,
                                         encoding="utf-8") as f:
            tmp = f.name
            json.dump(data, f, indent=1)
        os.replace(tmp, store)
    except OSError:
        if tmp is not None:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def expand(text: str) -> str:
    """`text` with `{cad}` and `{code}` replaced by this computer's folders. Text without them is unchanged."""
    if not _PLACEHOLDER.search(text):
        return text
    known = folders()

    def one(m: re.Match) -> str:
        name = m.group(1)
        path = known[name]
        if path is None:
            raise TeamFolderError(
                f"this robot file uses the team's {_LABEL[name]} folder, but this computer doesn't know where it is: "
                f"set it on the start page (scrimmage edit), or set SCRIMMAGE_{name.upper()}")
        return str(path)

    return _PLACEHOLDER.sub(one, text)


def relativize(path: Path) -> str:
    """`path` as `{cad}/...` or `{code}/...` when it is under a team folder (the longest folder wins), else as is."""
    p = Path(path)
    best: tuple[int, str, Path] | None = None
    for name, root in folders().items():
        if root is None:
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        if best is None or len(root.parts) > best[0]:
            best = (len(root.parts), name, rel)
    if best is None:
        return str(path)
    rel = best[2].as_posix()
    return f"{{{best[1]}}}" if rel == "." else f"{{{best[1]}}}/{rel}"
