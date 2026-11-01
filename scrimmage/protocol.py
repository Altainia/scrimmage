"""WPILib HALSim WebSocket protocol helpers.

Spec: https://github.com/wpilibsuite/allwpilib/blob/main/simulation/halsim_ws_core/doc/hardware_ws_api.md

Every frame is one JSON object: {"type": ..., "device": ..., "data": {...}}.
Data keys are prefixed: "<" = output from robot code, ">" = input to robot code, "<>" = both.
Data is sent as deltas, so we keep a merged view of every device.
"""

from __future__ import annotations

import json
import math
import time
from collections import deque
from typing import Any, Callable, Iterable

DeviceKey = tuple[str, str]  # (type, device)
# Sent values remembered per input, to recognize the robot program relaying them back: always the last
# SENT_HISTORY, and any sent within a window of wall time, at most SENT_CAP. The window is SENT_WINDOW (a JVM GC or
# JIT pause holds echoes back for many real-time sends, 5 ms apart) plus twice the longest the robot program has been
# behind this run: silent (nothing heard from it, up to now), or relaying a value after a newer one had gone out.
# A robot program that stalls (a starved CI runner) relays everything sent meanwhile once it resumes; then Phoenix 6
# rewrites the same values again from its own state, trailing (measured after a 3 s stall: the backlog relayed from
# 3.0 s late, Phoenix's rewrites of it from 4.1 s late). SENT_CAP covers about 20 s of 5 ms sends. Lockstep sends
# once per step, so its last 8 cover it.
# A starved robot program need not go silent, though: its HALSim thread can keep relaying frames while Phoenix's
# rewrites of our values fall seconds behind, before any late relay has shown the lag (CI: 3.2 s, every Talon whose
# values were changing flagged at once). So sent values are kept LATE_ECHO back whatever the lag: a robot-side model
# writes every loop, and its values would have to match ours of the last LATE_ECHO s every time to pass. SENT_CAP
# covers about 20 s of 5 ms sends.
SENT_HISTORY = 8
SENT_WINDOW = 1.0
LATE_ECHO = 10.0
SENT_CAP = 4096


class HalState:
    """Merged view of everything the robot program has told us."""

    def __init__(self) -> None:
        self.devices: dict[DeviceKey, dict[str, Any]] = {}

    def apply(self, raw: str | bytes) -> tuple[DeviceKey, dict[str, Any]] | None:
        """Merge one frame; returns ((type, device), delta) or None if the frame was invalid."""
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(msg, dict):
            return None
        typ, dev, data = msg.get("type"), msg.get("device"), msg.get("data")
        if not isinstance(typ, str) or not isinstance(dev, str) or not isinstance(data, dict):
            return None
        self.devices.setdefault((typ, dev), {}).update(data)
        return (typ, dev), data

    def get(self, typ: str, device: str, key: str, default: Any = None) -> Any:
        return self.devices.get((typ, device), {}).get(key, default)

    def find(self, typ: str, key: str, value: Any) -> str | None:
        """Return the device id of the first `typ` device whose `key` equals `value`."""
        for (t, dev), data in self.devices.items():
            if t == typ and data.get(key) == value:
                return dev
        return None


def frame(typ: str, device: str, data: dict[str, Any]) -> str:
    return json.dumps({"type": typ, "device": device, "data": data}, separators=(",", ":"))


class OutboundCache:
    """Only resend input values that actually changed, to keep the socket quiet. Also remembers the last
    values sent for each input (sent_history: the last SENT_HISTORY, plus those within the window of wall time
    above), so values relayed back can be recognized. The bridge calls heard() for every frame from the robot
    program and relayed() for every value recognized."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._last: dict[tuple[str, str, str], Any] = {}
        self._history: dict[tuple[str, str, str], deque] = {}  # (wall time, value), oldest first
        # The longest the robot program has been behind (wall s), counted from track_lag() on: once Phoenix 6 frees
        # its clock. Before that (booting, held in lockstep) it may be silent as long as it likes, and lockstep sends
        # once per step, so the last SENT_HISTORY cover its echoes. A maximum over the rest of the connection, not a
        # decaying one: a robot program that has stalled once on this machine can stall again, and a wider window
        # costs only a little detection (a robot-side model writes every loop, so it still shows).
        self.lag = 0.0
        self._tracking = False
        self._heard_at: float | None = None   # when the last frame from the robot program arrived (while tracking)

    def clear(self) -> None:
        """Resend every input (the sent history is kept)."""
        self._last.clear()

    def reset(self) -> None:
        """Resend every input and forget what was sent (a new run)."""
        self._last.clear()
        self._history.clear()
        self.lag = 0.0
        self._tracking = False
        self._heard_at = None

    def track_lag(self) -> None:
        """From now on, count how far behind the robot program falls (it runs in real time). Silence before this
        doesn't count: the next frame starts the clock."""
        self._tracking = True
        self._heard_at = None

    def forget(self, typ: str, device: str) -> None:
        """Resend everything for one device (e.g. it was just (re)initialized and lost our values)."""
        for k in [k for k in self._last if k[0] == typ and k[1] == device]:
            del self._last[k]
        for k in [k for k in self._history if k[0] == typ and k[1] == device]:
            del self._history[k]

    def sent_history(self, typ: str, device: str, key: str) -> list:
        """The last values sent for one input, oldest first ([] if none was ever sent)."""
        hist = self._history.get((typ, device, key))
        if hist is None:
            return []
        self._prune(hist, self.clock())
        return [v for _, v in hist]

    def heard(self) -> float:
        """A frame arrived from the robot program: it was behind for as long as it had been silent. Returns that
        (0 before track_lag())."""
        if not self._tracking:
            return 0.0
        now = self.clock()
        gap = now - self._heard_at if self._heard_at is not None else 0.0
        self.lag = max(self.lag, gap)
        self._heard_at = now
        return gap

    def relayed(self, typ: str, device: str, key: str, index: int) -> float:
        """The robot program relayed back the value at `index` of the sent_history() just returned: if a newer
        value had gone out, it was at least that long behind. Returns that (0 if it relayed the newest, or before
        track_lag())."""
        hist = self._history.get((typ, device, key))
        if not self._tracking or hist is None or not 0 <= index < len(hist) - 1:
            return 0.0
        behind = self.clock() - hist[index + 1][0]
        self.lag = max(self.lag, behind)
        return behind

    def window(self, now: float) -> float:
        """How far back (wall s) sent values are kept beyond the last SENT_HISTORY."""
        silent = now - self._heard_at if self._heard_at is not None else 0.0
        return max(SENT_WINDOW + 2.0 * max(self.lag, silent), LATE_ECHO)

    def _prune(self, hist: deque, now: float) -> None:
        window = self.window(now)
        while len(hist) > SENT_HISTORY and now - hist[0][0] > window:
            hist.popleft()

    def diff(self, typ: str, device: str, data: dict[str, Any]) -> dict[str, Any]:
        changed = {}
        for k, v in data.items():
            key = (typ, device, k)
            old = self._last.get(key)
            if old is None or _changed(old, v):
                changed[k] = v
                self._last[key] = v
                hist = self._history.setdefault(key, deque(maxlen=SENT_CAP))
                if not hist or hist[-1][1] != v:  # a full refresh resends the same value: keep the history deep
                    now = self.clock()
                    hist.append((now, v))
                    self._prune(hist, now)
        return changed

    def frames(self, updates: Iterable[tuple[str, str, dict[str, Any]]]) -> list[str]:
        out = []
        for typ, dev, data in updates:
            d = self.diff(typ, dev, data)
            if d:
                out.append(frame(typ, dev, d))
        return out


def _changed(old: Any, new: Any) -> bool:
    if isinstance(old, float) and isinstance(new, float):
        if math.isinf(old) or math.isinf(new):
            return old != new
        return abs(old - new) > 1e-9 * max(1.0, abs(old))
    return old != new
