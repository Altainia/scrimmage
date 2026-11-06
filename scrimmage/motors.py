"""Brushed/brushless DC motor models using the same constants and equations as WPILib's DCMotor.

All units are SI: volts, amps, newton-meters, radians per second.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import i18n

RPM_TO_RAD_S = 2.0 * math.pi / 60.0


@dataclass(frozen=True)
class DCMotor:
    """A single motor (or several identical motors ganged on one gearbox, see `gearbox`)."""

    nominal_voltage: float
    stall_torque: float
    stall_current: float
    free_current: float
    free_speed: float  # rad/s
    count: int = 1

    @property
    def R(self) -> float:
        """Winding resistance (ohms)."""
        return self.nominal_voltage / self.stall_current

    @property
    def Kv(self) -> float:
        """Velocity constant (rad/s per volt)."""
        return self.free_speed / (self.nominal_voltage - self.R * self.free_current)

    @property
    def Kt(self) -> float:
        """Torque constant (N*m per amp)."""
        return self.stall_torque / self.stall_current

    def gearbox(self, count: int) -> "DCMotor":
        """Return a model of `count` of these motors sharing one output shaft."""
        return DCMotor(
            self.nominal_voltage,
            self.stall_torque * count,
            self.stall_current * count,
            self.free_current * count,
            self.free_speed,
            count,
        )

    def current(self, speed: float, voltage: float) -> float:
        """Total current (amps) drawn at motor shaft `speed` (rad/s) with `voltage` applied."""
        return (voltage - speed / self.Kv) / self.R

    def torque(self, current: float) -> float:
        """Total shaft torque (N*m) produced by `current` amps."""
        return self.Kt * current


def _m(v, stall_torque, stall_current, free_current, free_speed_rpm) -> DCMotor:
    return DCMotor(v, stall_torque, stall_current, free_current, free_speed_rpm * RPM_TO_RAD_S)


# Constants match edu.wpi.first.math.system.plant.DCMotor (WPILib 2026).
MOTORS: dict[str, DCMotor] = {
    "CIM": _m(12, 2.42, 133, 2.7, 5310),
    "MiniCIM": _m(12, 1.41, 89, 3, 5840),
    "Bag": _m(12, 0.43, 53, 1.8, 13180),
    "Vex775Pro": _m(12, 0.71, 134, 0.7, 18730),
    "NEO": _m(12, 2.6, 105, 1.8, 5676),
    "NEO550": _m(12, 0.97, 100, 1.4, 11000),
    "NeoVortex": _m(12, 3.60, 211, 3.6, 6784),
    "Falcon500": _m(12, 4.69, 257, 1.5, 6380),
    "Falcon500FOC": _m(12, 5.84, 304, 1.5, 6080),
    "KrakenX60": _m(12, 7.09, 366, 2, 6000),
    "KrakenX60FOC": _m(12, 9.37, 483, 2, 5800),
}
# Add your own with: MOTORS["MyMotor"] = _m(volts, stall_Nm, stall_A, free_A, free_rpm)


def get_motor(name: str, count: int = 1) -> DCMotor:
    try:
        base = MOTORS[name]
    except KeyError:
        raise ValueError(i18n.t("Unknown motor '{name}'. Known motors: {motors}",
                                name=name, motors=', '.join(sorted(MOTORS)))) from None
    return base.gearbox(count)


@dataclass
class Battery:
    """Very small battery model matching WPILib's BatterySim: V = V_nominal - I_total * R."""

    nominal_voltage: float = 12.0
    resistance: float = 0.02
    brownout_voltage: float = 6.8

    def voltage(self, total_current: float) -> float:
        return max(0.0, self.nominal_voltage - total_current * self.resistance)
