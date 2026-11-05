"""Candidate config text -> the real loader and assembler -> errors and warnings keyed by GUI field."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigError, build_world, load_config_text


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str
    code: str | None = field(default=None, compare=False)   # what the error is, for code that mustn't read its words


@dataclass
class Result:
    ok: bool
    errors: list[FieldError] = field(default_factory=list)
    warnings: list[FieldError] = field(default_factory=list)
    world: object = None


_PART = re.compile(r"parts\[(\d+)\] '([^']*)': (.*)", re.S)
_IDS_NO_SLOT = re.compile(r"ids: no slot '([^']*)'")
_IDS_SLOT = re.compile(r"ids: (?:slot )?'?([A-Za-z0-9_]+)'?")
_BOUND = re.compile(r"is bound by \w+ ([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)(?:\[\d+\])? and \w+ "
                    r"([A-Za-z0-9_]+)\.([A-Za-z0-9_]+)(?:\[\d+\])?")
_NO_ID = re.compile(r"([A-Za-z0-9_]+)\.([A-Za-z0-9_]+) has no device ID")


def _strip_path(message: str, path) -> str:
    for p in {str(path), str(Path(path).resolve())}:
        if message.startswith(p + ": "):
            return message[len(p) + 2:]
    return message


def _param_names(raw: dict, index: int) -> list[str]:
    try:
        entry = raw["part"][index]
        stock = entry.get("stock")
        if stock is None:
            return []
        from ..parts.stock import REGISTRY, _load_all
        _load_all()
        return [p.name for p in REGISTRY[stock].params]
    except (KeyError, IndexError, TypeError, AttributeError):
        return []


def _map_error(message: str, raw: dict) -> list[FieldError]:
    m = _BOUND.search(message)
    if m:
        a, sa, b, sb = m.groups()
        return [FieldError(f"{a}/ids:{sa}", message), FieldError(f"{b}/ids:{sb}", message)]
    m = _PART.match(message)
    if not m:
        return [FieldError("status", message)]
    index, name, rest = int(m.group(1)), m.group(2), m.group(3)
    if rest.startswith("ids must"):
        return [FieldError(f"{name}/ids", message)]
    if rest.startswith("ids:"):
        slot = _IDS_NO_SLOT.match(rest) or _IDS_SLOT.match(rest)
        return [FieldError(f"{name}/ids:{slot.group(1)}" if slot else f"{name}/ids", message)]
    if rest.startswith("attach"):
        return [FieldError(f"{name}/attach", message)]
    if rest.startswith("name") or rest.startswith("the part name"):
        return [FieldError(f"{name}/name", message)]
    names = _param_names(raw, index)
    u = re.match(r"unknown parameter '([^']*)'", rest)
    if u:
        return [FieldError(f"{name}/param:{u.group(1)}", message)]
    first = rest.split(" ", 1)[0]
    if first in names:
        return [FieldError(f"{name}/param:{first}", message)]
    return [FieldError(f"{name}/part", message)]


def _map_warning(message: str) -> FieldError:
    m = _NO_ID.match(message)
    return FieldError(f"{m.group(1)}/ids:{m.group(2)}" if m else "status", message)


def check(text: str, path) -> Result:
    """Run `text` through load_config_text and build_world; never raises for a bad config."""
    raw: dict = {}
    try:
        cfg = load_config_text(text, path)
        raw = cfg.raw
        world = build_world(cfg)
    except ConfigError as e:
        return Result(False, _map_error(_strip_path(str(e), path), raw), [], None)
    except Exception as e:   # a bug-shaped failure still must not take the server down
        return Result(False, [FieldError("status", f"{type(e).__name__}: {e}")], [], None)
    return Result(True, [], [_map_warning(w) for w in world.warnings], world)
