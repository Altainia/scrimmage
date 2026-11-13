"""Test robot for scrimmage's RobotPy CANrange and LaserCAN test (tests/test_distance_py_e2e.py), the RobotPy twin of
tests/java_probe's "range" mode: CANranges 5 and 8 (phoenix6) and LaserCANs 7 and 9 (libgrapplefrc-py).

LaserCAN 7 comes from a `from libgrapplefrc import LaserCAN` at the top of this file, which `robotpy sim` runs before
it loads scrimmage's extension; LaserCAN 9 from `libgrapplefrc.LaserCAN` looked up when the robot is built. LaserCAN 9
is set to long ranging in robotInit and back to short in teleopInit, each printing "cfg lasercan <mode> ok" (or the
error). Every 5 loops it prints "range cr5=<m>,<detected> cr8=<m>,<detected> lc7=<reading> lc9=<reading> loop=<n>", a
LaserCAN reading being "null" or "<status>,<distance_mm>,<long|short>".
"""

import libgrapplefrc
import phoenix6
import wpilib
from libgrapplefrc import LaserCAN


def ranging(laser, mode) -> None:
    """Set the ranging mode and say how it went (Grapple's idiom: check the result's error)."""
    result = laser.set_range(mode)
    name = "Long" if mode == libgrapplefrc.LaserCanRangingMode.Long else "Short"
    print(f"cfg lasercan {name} " + ("ok" if result.error is None else f"failed: {result.error}"), flush=True)


def reading(laser) -> str:
    m = laser.get_measurement()
    if m is None:
        return "null"
    return f"{m.status},{m.distance_mm}," + ("long" if m.mode == libgrapplefrc.LaserCanRangingMode.Long else "short")


def flag(b) -> str:
    return "true" if b else "false"


class MyRobot(wpilib.TimedRobot):
    def robotInit(self) -> None:
        self.range5 = phoenix6.hardware.CANrange(5)
        self.range8 = phoenix6.hardware.CANrange(8)
        self.laser7 = LaserCAN(7)
        self.laser9 = libgrapplefrc.LaserCAN(9)
        self.loops = 0
        ranging(self.laser9, libgrapplefrc.LaserCanRangingMode.Long)

    def teleopInit(self) -> None:
        ranging(self.laser9, libgrapplefrc.LaserCanRangingMode.Short)

    def robotPeriodic(self) -> None:
        self.loops += 1
        if self.loops % 5 == 0:  # every 100 ms of robot time
            print(f"range cr5={self.range5.get_distance().value:.4f},{flag(self.range5.get_is_detected().value)} "
                  f"cr8={self.range8.get_distance().value:.4f},{flag(self.range8.get_is_detected().value)} "
                  f"lc7={reading(self.laser7)} lc9={reading(self.laser9)} loop={self.loops}", flush=True)
