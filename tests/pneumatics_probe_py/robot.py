"""Test robot for scrimmage's pneumatics tests (tests/test_pneumatics_robot_e2e.py): a single solenoid on a CTRE PCM's
channel 3, a double solenoid on a REV Pneumatic Hub's channels 4 and 5 (hub 1), and a compressor on the PCM. The hub
runs its compressor on its digital pressure switch (closed loop).

teleopInit turns the single solenoid on and the double solenoid forward. 1 s later the robot prints one line with
what it reads back: "pneumatics pcm_switch=<bool> ph_switch=<bool> ph_on=<bool> ph_amps=<A>".
"""

import wpilib

PCM = wpilib.PneumaticsModuleType.CTREPCM
PH = wpilib.PneumaticsModuleType.REVPH


class MyRobot(wpilib.TimedRobot):
    def robotInit(self) -> None:
        self.single = wpilib.Solenoid(PCM, 3)
        self.double = wpilib.DoubleSolenoid(PH, 4, 5)
        self.compressor = wpilib.Compressor(PCM)
        self.hub = wpilib.PneumaticHub(1)
        self.hub.enableCompressorDigital()
        self.start = None
        self.printed = False

    def teleopInit(self) -> None:
        self.single.set(True)
        self.double.set(wpilib.DoubleSolenoid.Value.kForward)
        self.start = wpilib.Timer.getFPGATimestamp()

    def teleopPeriodic(self) -> None:
        if not self.printed and wpilib.Timer.getFPGATimestamp() - self.start >= 1.0:
            self.printed = True
            print(f"pneumatics pcm_switch={self.compressor.getPressureSwitchValue()} "
                  f"ph_switch={self.hub.getPressureSwitch()} ph_on={self.hub.getCompressor()} "
                  f"ph_amps={self.hub.getCompressorCurrent():.2f}", flush=True)
