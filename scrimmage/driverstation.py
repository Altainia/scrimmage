"""Driver Station state that scrimmage sends to the robot program when it, not a human, is the DS."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .protocol import frame

MODES = ("disabled", "auto", "teleop", "test")
PHASES = ("auto", "teleop")   # the modes with a match timer
STATIONS = ("red1", "red2", "red3", "blue1", "blue2", "blue3")
MATCH_KEYS = ("station", "fms", "timer", "auto_seconds", "teleop_seconds")
GAME_DATA_MAX = 64            # the HAL keeps up to 64 bytes


def check_game_data(value, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where} game_data must be text, not {value!r}")
    if len(value) > GAME_DATA_MAX:
        raise ValueError(f"{where} game_data is {len(value)} characters long; it can be at most {GAME_DATA_MAX} "
                         "characters")
    return value


@dataclass(frozen=True)
class MatchSettings:
    """A [match] table: the station, FMS attached, and the match timer with each phase's length in seconds."""

    station: str = "blue1"
    fms: bool = False
    timer: bool = False
    auto_seconds: float = 20.0
    teleop_seconds: float = 140.0

    def merged(self, table: dict, where: str) -> MatchSettings:
        """A copy with `table`'s keys applied over these values. A bad key or value raises a ValueError that starts
        with `where`."""
        if not isinstance(table, dict):
            raise ValueError(f"{where} must be a table, not {table!r}")
        changes = {}
        for key, v in table.items():
            if key not in MATCH_KEYS:
                raise ValueError(f"{where} has no key {key!r}; the keys are {', '.join(MATCH_KEYS)}")
            if key == "station":
                if v not in STATIONS:
                    raise ValueError(f"{where} station must be one of {', '.join(STATIONS)}, not {v!r}")
            elif key in ("fms", "timer"):
                if not isinstance(v, bool):
                    raise ValueError(f"{where} {key} must be true or false, not {v!r}")
            else:
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                    raise ValueError(f"{where} {key} must be a positive number of seconds, not {v!r}")
                v = float(v)
            changes[key] = v
        return replace(self, **changes)


@dataclass
class Joystick:
    axes: list[float] = field(default_factory=list)
    buttons: list[bool] = field(default_factory=list)
    povs: list[int] = field(default_factory=lambda: [-1])


@dataclass
class DriverStationState:
    """active=False leaves the DS to someone else (the WPILib sim GUI): the bridge then sends no DS frames.
    changed asks the bridge to send on its next tick instead of waiting for the next DS period.

    The match time is worked out in frames(now) from the sim time of the last entry into Auto or Teleop (spec M1):
    a phase's length minus the time since, held while disabled, and -1 with the timer off, before any phase and in
    Test."""

    active: bool = False
    mode: str = "disabled"
    joysticks: dict[int, Joystick] = field(default_factory=dict)
    changed: bool = True
    match: MatchSettings = field(default_factory=MatchSettings)
    game_data: str = ""
    _seen_mode: str = "disabled"            # the mode at the last frame
    _entering: bool = False                 # a phase entry for the next frame to stamp
    _entry: float | None = None             # sim time the current phase started, or None for no countdown
    _length: float = 0.0                    # that phase's length
    _time: float = -1.0                     # the match time in the last frame (timer on)
    _extra_keys: bool = False               # >game_data and >fms go out once either has been set (M2)

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
        if mode in PHASES and mode != self.mode:
            self._entering = True
        self.mode = mode
        self.changed = True

    def set_match(self, **changes) -> None:
        """Change any of the [match] settings (MATCH_KEYS). Turning the timer on starts the current phase again."""
        new = self.match.merged(changes, "set_match:")
        if new.timer and not self.match.timer:
            self._entry, self._time = None, -1.0
            self._entering = self.mode in PHASES
        self.match = new
        self.changed = True

    def set_game_data(self, s: str) -> None:
        self.game_data = check_game_data(s, "set_game_data:")
        self._extra_keys = True
        self.changed = True

    def set_joystick(self, port: int, axes=None, buttons=None, povs=None) -> None:
        if not 0 <= int(port) <= 5:
            raise ValueError(f"joystick port must be 0-5, not {port}")
        js = self.joysticks.setdefault(int(port), Joystick())
        if axes is not None:
            js.axes = [float(a) for a in axes]
        if buttons is not None:
            js.buttons = [bool(b) for b in buttons]
        if povs is not None:
            js.povs = [int(p) for p in povs]
        self.changed = True

    def clear_joysticks(self) -> None:
        """Neutral inputs (zero axes, no buttons, POV centered) on every port used so far. The ports stay in the
        dict so the neutral frames go out: dropping them would leave the robot with its last inputs."""
        for js in self.joysticks.values():
            js.axes = [0.0] * len(js.axes)
            js.buttons = [False] * len(js.buttons)
            js.povs = [-1]
        self.changed = True

    def clear(self) -> None:
        """Back to the start of a match (M3): disabled, neutral joysticks, no match time and no game data. The
        match settings stay."""
        self.mode = "disabled"
        self.clear_joysticks()
        self._entering, self._entry, self._time = False, None, -1.0
        self.game_data = ""
        self.changed = True

    @property
    def sent_match_time(self) -> float:
        """The match time in the last frame."""
        return self._time if self.match.timer else -1.0

    def _tick(self, now: float) -> None:
        m, before = self.mode, self._seen_mode
        if m in PHASES and (self._entering or m != before):
            self._entry = now
            self._length = self.match.auto_seconds if m == "auto" else self.match.teleop_seconds
        elif m == "test":
            self._entry, self._time = None, -1.0
        self._entering = False
        self._seen_mode = m
        # A disable counts the time up to the frame that first sees it, then holds.
        if self._entry is not None and (m in PHASES or before in PHASES):
            self._time = max(0.0, round(self._length - (now - self._entry), 3))

    def frames(self, now: float) -> list[str]:
        """The frames to send at sim time `now`."""
        self._tick(now)
        out = [frame("Joystick", str(p), {">axes": j.axes, ">buttons": j.buttons, ">povs": j.povs})
               for p, j in sorted(self.joysticks.items())]
        m = self.mode
        data = {">enabled": m != "disabled", ">autonomous": m == "auto", ">test": m == "test",
                ">ds": True, ">station": self.match.station, ">match_time": self.sent_match_time}
        if self._extra_keys or self.match.fms or self.game_data:
            self._extra_keys = True
            data[">fms"] = self.match.fms
            data[">game_data"] = self.game_data
        data[">new_data"] = True
        out.append(frame("DriverStation", "", data))
        return out
