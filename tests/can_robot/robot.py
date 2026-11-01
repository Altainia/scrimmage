"""Test robot: two CAN 'controllers' whose sim state is a few variables, republished via WsCanShim."""

import sys
from pathlib import Path

import wpilib

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shims" / "python"))
from ws_can_shim import WsCanShim  # noqa: E402


class FakeController:
    """Stands in for a vendor sim state (e.g. phoenix6 TalonFXSimState)."""

    def __init__(self):
        self.duty = 0.0
        self.supply = 12.0
        self.rotations = 0.0
        self.rps = 0.0

    def motor_voltage(self):
        return self.duty * self.supply


class MyRobot(wpilib.TimedRobot):
    def robotInit(self):
        self.ctrl = {1: FakeController(), 2: FakeController()}
        self.shims = []
        for cid, c in self.ctrl.items():
            self.shims.append(WsCanShim(
                f"Fake[{cid}]", applied_volts=c.motor_voltage,
                set_supply_voltage=lambda v, c=c: setattr(c, "supply", v),
                set_rotor_rotations=lambda r, c=c: setattr(c, "rotations", r),
                set_rotor_rps=lambda r, c=c: setattr(c, "rps", r)))
        self.n = 0
        self.limit = wpilib.DigitalInput(9)
        self.pot = wpilib.AnalogInput(0)

    def autonomousPeriodic(self):
        self.ctrl[1].duty = 0.5   # left, normal
        self.ctrl[2].duty = -0.5  # right, mirrored -> also forward
        self.n += 1
        if self.n % 10 == 0:
            a, b = self.ctrl[1], self.ctrl[2]
            print(f"CAN rot={a.rotations:.3f},{b.rotations:.3f} rps={a.rps:.3f},{b.rps:.3f} vbus={a.supply:.2f} "
                  f"dio={self.limit.get()} ai={self.pot.getVoltage():.3f}",
                  flush=True)

    def disabledPeriodic(self):
        for c in self.ctrl.values():
            c.duty = 0.0
        self.n += 1
        if self.n % 10 == 0:
            print(f"IO dio={self.limit.get()} ai={self.pot.getVoltage():.3f}", flush=True)

    def _simulationPeriodic(self):  # RobotPy's name for WPILib's simulationPeriodic()
        for s in self.shims:
            s.update()
