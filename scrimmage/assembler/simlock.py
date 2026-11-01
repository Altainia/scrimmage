"""One simulation at a time: a live drive session, a scenario run or a bench job, never two."""

from __future__ import annotations

import threading
from typing import Literal

Owner = Literal["run", "live", "bench"]

BUSY = {"run": "a scenario run is in progress", "live": "a drive session is live",
        "bench": "a bench run is in progress"}


class SimBusy(Exception):
    """Another simulation holds the lock; the message says which."""


class SimLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: str | None = None

    def acquire(self, owner: Owner) -> None:
        if owner not in BUSY:
            raise ValueError(f"owner must be one of {', '.join(BUSY)}, not {owner!r}")
        with self._lock:
            if self._holder is not None:
                raise SimBusy(BUSY[self._holder])
            self._holder = owner

    def release(self, owner: Owner) -> None:
        """Releases the lock if `owner` holds it; otherwise does nothing."""
        with self._lock:
            if self._holder == owner:
                self._holder = None

    @property
    def holder(self) -> str | None:
        with self._lock:
            return self._holder
