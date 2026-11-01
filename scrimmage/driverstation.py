"""Driver Station state that scrimmage sends to the robot program when it, not a human, is the DS."""

from __future__ import annotations

from dataclasses import dataclass, field

from .protocol import frame

MODES = ("disabled", "auto", "teleop", "test")


@dataclass
class Joystick:
    axes: list[float] = field(default_factory=list)
    buttons: list[bool] = field(default_factory=list)
    povs: list[int] = field(default_factory=lambda: [-1])


@dataclass
class DriverStationState:
    """active=False leaves the DS to someone else (the WPILib sim GUI): the bridge then sends no DS frames.
    changed asks the bridge to send on its next tick instead of waiting for the next DS period."""

    active: bool = False
    mode: str = "disabled"
    match_time: float = -1.0
    joysticks: dict[int, Joystick] = field(default_factory=dict)
    changed: bool = True

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
        self.mode = mode
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
        self.mode = "disabled"
        self.clear_joysticks()
        self.match_time = -1.0
        self.changed = True

    def frames(self) -> list[str]:
        out = [frame("Joystick", str(p), {">axes": j.axes, ">buttons": j.buttons, ">povs": j.povs})
               for p, j in sorted(self.joysticks.items())]
        m = self.mode
        out.append(frame("DriverStation", "", {
            ">enabled": m != "disabled", ">autonomous": m == "auto", ">test": m == "test",
            ">ds": True, ">station": "blue1", ">match_time": self.match_time, ">new_data": True}))
        return out
