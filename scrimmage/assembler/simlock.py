"""One simulation at a time: a live drive session, a scenario run, a bench job or a block test, never two."""

from __future__ import annotations

import threading
from typing import Literal

Owner = Literal["run", "live", "bench", "test"]

BUSY = {"run": "a scenario run is in progress", "live": "a drive session is live",
        "bench": "a bench run is in progress", "test": "a test run is in progress"}
# what to tell someone who wants the simulation while `owner` holds it
WAIT = {"run": "Wait for the scenario run to finish first.", "live": "Wait for the drive session to stop first.",
        "bench": "Wait for the bench run to finish first.", "test": "Wait for the test run to finish first."}


class SimBusy(Exception):
    """Another simulation holds the lock; the message says which, and `holder` is its owner."""

    def __init__(self, message: str, holder: str | None = None):
        super().__init__(message)
        self.holder = holder

    @property
    def wait(self) -> str:
        """The lock's words as a plain sentence: "Wait for the drive session to stop first." """
        return WAIT.get(self.holder, "Wait for the other simulation to finish first.")


class SimLock:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._holder: str | None = None

    def acquire(self, owner: Owner) -> None:
        if owner not in BUSY:
            raise ValueError(f"owner must be one of {', '.join(BUSY)}, not {owner!r}")
        with self._lock:
            if self._holder is not None:
                raise SimBusy(BUSY[self._holder], self._holder)
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
