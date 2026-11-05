"""The config file as an editable, undoable tomlkit document."""

from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path
from typing import Callable

import tomlkit
from tomlkit.exceptions import TOMLKitError

from .. import i18n
from .edits import EditError


_MAX_HISTORY = 200


class FileChanged(EditError):
    """The file on disk changed since it was last loaded; nothing was written."""

    def __init__(self):
        super().__init__("status", i18n.t("the file changed on disk; the page has reloaded it"))


class ConfigDoc:
    def __init__(self, path):
        self.path = Path(path).resolve()   # writes go to the real target, never replace a symlink
        self.read_only_reason: str | None = None
        self.version = 0
        self.doc = None
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._load()

    def _load(self) -> None:
        self._raw = self.path.read_bytes()
        try:
            self.text = self._raw.decode("utf-8")
            self.doc = tomlkit.parse(self.text)
            self.read_only_reason = None
        except (UnicodeDecodeError, TOMLKitError) as e:
            self.text = self._raw.decode("utf-8", errors="replace")
            self.doc = None
            self.read_only_reason = i18n.t("{name} is not valid TOML: {why}", name=self.path.name, why=e)

    @property
    def can_undo(self) -> bool:
        return bool(self._undo) and self.read_only_reason is None

    @property
    def can_redo(self) -> bool:
        return bool(self._redo) and self.read_only_reason is None

    def _require_writable(self) -> None:
        if self.read_only_reason is not None:
            raise EditError("status", self.read_only_reason)

    def apply(self, fn: Callable) -> str:
        """Candidate text from applying fn to a copy of the document; nothing is committed."""
        self._require_writable()
        d = copy.deepcopy(self.doc)
        fn(d)
        return tomlkit.dumps(d)

    def _write(self, text: str) -> None:
        try:
            tomlkit.parse(text)
        except TOMLKitError as e:
            raise EditError("status", i18n.t("refusing to write invalid TOML: {why}", why=e)) from e
        fd = tempfile.NamedTemporaryFile(dir=self.path.parent, delete=False,
                                         prefix=self.path.name + ".", suffix=".tmp")
        try:
            with fd:
                fd.write(text.encode("utf-8"))
                fd.flush()
                os.fsync(fd.fileno())
            try:
                if self.path.read_bytes() != self._raw:   # narrow the race with an outside editor
                    raise FileChanged()
            except FileNotFoundError:
                pass
            if self.path.exists():
                os.chmod(fd.name, self.path.stat().st_mode & 0o7777)
            os.replace(fd.name, self.path)
        except BaseException:
            try:
                os.unlink(fd.name)
            except FileNotFoundError:
                pass
            raise
        self.text = text
        self._raw = text.encode("utf-8")
        self.doc = tomlkit.parse(text)
        self.version += 1

    @staticmethod
    def _push(stack: list[str], text: str) -> None:
        stack.append(text)
        del stack[:-_MAX_HISTORY]

    def peek_undo(self) -> str | None:
        """The text undo() would restore, or None."""
        return self._undo[-1] if self._undo else None

    def peek_redo(self) -> str | None:
        return self._redo[-1] if self._redo else None

    def commit(self, text: str) -> None:
        self._require_writable()
        previous = self.text
        self._write(text)
        self._push(self._undo, previous)
        self._redo.clear()

    def undo(self) -> bool:
        self._require_writable()
        if not self._undo:
            return False
        current = self.text
        self._write(self._undo[-1])
        self._undo.pop()
        self._push(self._redo, current)
        return True

    def redo(self) -> bool:
        self._require_writable()
        if not self._redo:
            return False
        current = self.text
        self._write(self._redo[-1])
        self._redo.pop()
        self._push(self._undo, current)
        return True

    def reload_if_changed(self) -> bool:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return False
        if raw == self._raw:
            return False
        self._undo.clear()
        self._redo.clear()
        self._load()
        self.version += 1
        return True
