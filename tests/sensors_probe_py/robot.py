"""Test robot for scrimmage's sensors tests (tests/test_sensors_robot_e2e.py): a beam break on DIO 0, an analog
distance sensor on AI 0 and a REV Color Sensor V3 on the MXP I2C port.

Every 0.25 s it prints one line with what it reads: "sensors beam=<bool> volts=<V> raw=<r>,<g>,<b>,<ir>
color=<r>,<g>,<b> prox=<n>".
"""

import rev
import wpilib


class MyRobot(wpilib.TimedRobot):
    def robotInit(self) -> None:
        self.beam = wpilib.DigitalInput(0)
        self.range = wpilib.AnalogInput(0)
        self.eye = rev.ColorSensorV3(wpilib.I2C.Port.kMXP)
        self.loops = 0

    def robotPeriodic(self) -> None:
        self.loops += 1
        if self.loops % 12 == 0:
            raw = self.eye.getRawColor()
            color = self.eye.getColor()
            print(f"sensors beam={self.beam.get()} volts={self.range.getVoltage():.4f} "
                  f"raw={raw.red},{raw.green},{raw.blue},{raw.ir} "
                  f"color={color.red:.4f},{color.green:.4f},{color.blue:.4f} prox={self.eye.getProximity()}",
                  flush=True)
