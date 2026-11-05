"""The safety net: recognizing simulated Phoenix 6 inputs that scrimmage didn't send, and explaining them."""

from __future__ import annotations

from typing import Any

from . import i18n
from .i18n import N_

COUNT = 1 / 4096          # Phoenix 6 position resolution, in rotations
FLOAT32 = 2 * 2.0 ** -23  # Phoenix rewrites inputs as float32: allow two ulps of the larger magnitude
ROTOR = "/Rotor Sensor"

# How far a value the robot program relays back may be from one scrimmage sent and still be its echo.
# halsim_ws_client relays every value scrimmage sends right back, and Phoenix 6 rewrites some inputs itself
# once per robot loop, quantized (measured with Phoenix 6 26.1.2): the rotor position up to 1 count off,
# the CANcoder's up to 1/2 count, velocities in steps of 20/4096 (rotor) and 10/4096 rps (CANcoder), the
# Talon's supply voltage up to 7 mV below, and the Pigeon's yaw wrapped to [0, 360). All as float32: a rotor
# position of 20000 rev comes back float32-rounded (not wrapped), a yaw of 20000 deg float32-rounded and wrapped.


def _tolerance(device: str, key: str, value: float, sent: float) -> float:
    f32 = FLOAT32 * max(abs(value), abs(sent))
    if key == ">rawPositionInput":
        return (2 * COUNT if device.endswith(ROTOR) else COUNT) + 1e-9 + f32
    if key == ">velocity":
        return 20 * COUNT + 1e-9 + f32
    if key == ">supplyVoltage":
        return 0.02 + f32
    if key == ">rawYawInput":
        return 1e-3 + f32
    return max(1e-6, 1e-6 * abs(value)) + f32


def is_echo(device: str, key: str, value: Any, sent: list) -> bool:
    """Is `value`, relayed back by the robot program, explained by one of the values scrimmage `sent`?"""
    return echo_index(device, key, value, sent) is not None


def echo_index(device: str, key: str, value: Any, sent: list) -> int | None:
    """The index in `sent` (oldest first) of the newest value that explains `value`, or None. The newest: two
    values within the tolerance of each other both explain it, and the newer says the least about how far behind
    the robot program is."""
    num = isinstance(value, (int, float)) and not isinstance(value, bool)
    for i in range(len(sent) - 1, -1, -1):
        s = sent[i]
        if not num or isinstance(s, bool) or not isinstance(s, (int, float)):
            if value == s:
                return i
            continue
        d = value - s
        if key == ">rawYawInput":
            d = (d + 180.0) % 360.0 - 180.0
        if abs(d) <= _tolerance(device, key, value, s):
            return i
    return None


_WORDS = {">rawPositionInput": N_("position"), ">velocity": N_("velocity"), ">supplyVoltage": N_("supply voltage"),
          ">rawYawInput": N_("yaw"), ">pitch": N_("pitch"), ">roll": N_("roll"),
          ">angularVelX": N_("angular velocity X"), ">angularVelY": N_("angular velocity Y"),
          ">angularVelZ": N_("angular velocity Z")}


def key_words(device: str, key: str) -> str:
    """`>rawPositionInput` on a Rotor Sensor -> "rotor position", and so on."""
    if device.endswith(ROTOR) and key == ">rawPositionInput":
        return i18n.t("rotor position")
    if device.endswith(ROTOR) and key == ">velocity":
        return i18n.t("rotor velocity")
    return i18n.translated(_WORDS[key]) if key in _WORDS else key.lstrip("<>")


def device_name(device: str) -> str:
    """The device as robot code names it: "Talon FX (v6)[1]/Rotor Sensor" -> "Talon FX (v6)[1]"."""
    return device.removesuffix(ROTOR)


FIX = N_("Fix: remove or guard the robot-side sim model, e.g. startSimThread(), updateSimState(…), or a "
         "simulationPeriodic() that calls getSimState().set…")
FIX_PYTHON = N_("Fix: remove or guard the robot-side sim model, e.g. physics.py or a simulationPeriodic() that calls "
                "a device's sim_state.set_…()")
CAUSE_PYTHON = N_("RobotPy robots run without the scrimmage agent, so robot-side Phoenix sim writes "
                  "(sim_state.set_…) reach the device")


def cause(agent: str | None, language: str | None = None) -> str:
    """Why robot-side writes got through: for a RobotPy robot, always its own sim model; otherwise from the Java
    agent's console lines ("loaded", "disabled", "missing", or None when the console isn't visible)."""
    if language == "python":
        return i18n.translated(CAUSE_PYTHON)
    if agent == "disabled":
        return i18n.t("the scrimmage agent was disabled (-Dscrimmage.agent.disable), so robot-side sim writes reach "
                      "the device")
    if agent == "missing":
        # no agent line yet; Gradle passes the console on late, so it may only not have arrived
        return i18n.t("the robot console hasn't said the scrimmage Java agent is loaded (yet): if it isn't, run "
                      "`scrimmage java build` if it isn't built, and run the robot program through scrimmage or its "
                      "Gradle init script (see `scrimmage java path`)")
    if agent == "loaded":
        return i18n.t("the scrimmage agent is loaded, so something writes this device's sim values without going "
                      "through the Phoenix SimState setters (e.g. a SimDeviceSim or HAL write)")
    return i18n.t("check that the scrimmage Java agent is built (`scrimmage java build`) and loaded "
                  "(see `scrimmage java path`)")


def by_device(writes: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for w in writes:
        out.setdefault(device_name(w["device"]), []).append(w)
    return out


def message(device: str, writes: list[dict], agent: str | None, language: str | None = None) -> str:
    """One device's foreign writes, explained: what, why, and how to fix it ("python" for a RobotPy robot)."""
    what = ", ".join(dict.fromkeys(key_words(w["device"], w["key"]) for w in writes))
    t = min(w["first_t"] for w in writes)
    fix = FIX_PYTHON if language == "python" else FIX
    return i18n.t("something in the robot program writes simulated sensor values for {device} ({what}, first at "
                  "t={time}), so physics and robot code disagree; {cause}. {fix}", device=device, what=what,
                  time=f"{t:.3f}", cause=cause(agent, language), fix=i18n.translated(fix))
