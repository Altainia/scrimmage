"""Candidate config text -> the real loader and assembler -> errors and warnings keyed by GUI field."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import ConfigError, build_world, load_config_text


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str
    code: str | None = field(default=None, compare=False)   # what the error is, for code that mustn't read its words
    detail: str | None = field(default=None, compare=False)  # the message without where it is (a scenario's errors)


@dataclass
class Result:
    ok: bool
    errors: list[FieldError] = field(default_factory=list)
    warnings: list[FieldError] = field(default_factory=list)
    world: object = None


def _strip_path(message: str, path) -> str:
    for p in {str(path), str(Path(path).resolve())}:
        if message.startswith(p + ": "):
            return message[len(p) + 2:]
    return message


def _placed(fields) -> list[str]:
    """The Robot tab's field ids for an error's or warning's (part, field) pairs (PartError.fields)."""
    return [f"{part}/{name}" for part, name in fields]


def _map_error(message: str, fields=()) -> list[FieldError]:
    """`message` on the fields its error names (spec L3: by its fields, not its words), else on the status line."""
    return [FieldError(f, message) for f in _placed(fields)] or [FieldError("status", message)]


def _map_warning(message: str) -> FieldError:
    """A warning on its field (a parts.model.Noted names it), else on the status line."""
    placed = _placed(getattr(message, "fields", ()))
    return FieldError(placed[0] if placed else "status", str(message))


def check(text: str, path) -> Result:
    """Run `text` through load_config_text and build_world; never raises for a bad config."""
    try:
        cfg = load_config_text(text, path)
        world = build_world(cfg)
    except ConfigError as e:
        return Result(False, _map_error(_strip_path(str(e), path), e.fields), [], None)
    except Exception as e:   # a bug-shaped failure still must not take the server down
        return Result(False, [FieldError("status", f"{type(e).__name__}: {e}")], [], None)
    return Result(True, [], [_map_warning(w) for w in world.warnings], world)
