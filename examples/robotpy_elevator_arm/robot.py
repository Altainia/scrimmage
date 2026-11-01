"""A plain WPILib robot with an elevator and an arm: nothing in here knows it's being simulated.

Hardware:
  PWM 0 / 1        drivetrain motor controllers (unused here)
  PWM 2 / DIO 4,5  elevator motor controller / encoder on the spool, 2048 counts per rev
  PWM 3 / DIO 6,7  arm motor controller / encoder on the arm shaft, 2048 counts per rev

The elevator is a two-stage cascade, so the carriage rises (spool turns x 2 pi r x 2) meters.
Autonomous: PID the carriage to 0.6 m and the arm to 45 degrees, with a gravity feedforward on each,
and print a line when each is within tolerance. Teleop does nothing.
"""

import math

import wpilib
from wpimath.controller import PIDController

SPOOL_RADIUS_M = 0.022
STAGES = 2
ENCODER_CPR = 2048
TARGET_HEIGHT_M = 0.6
TARGET_ARM_DEG = 45.0

# Feedforward volts to hold against gravity, from  V = (torque at the motor / Kt) * R  (a stalled motor).
# Elevator (2-stage cascade): the rope pulls the first stage (travel q) while the carriage rises 2q, so
# rope force = g * (2 * (3 kg carriage + 2 kg arm) + 1.5 kg stage) = 112.8 N; at the spool that is 112.8 * 0.022 m,
# split over 2 Kraken X60s at 20:1 (Kt 0.01937 N*m/A, R 0.03279 ohm):
#   ELEVATOR_KG = 112.8 * 0.022 / (20 * 2 * 0.01937) * 0.03279 = 0.105 V
# Arm: a 2 kg, 0.5 m bar has its weight at 0.25 m; one Falcon 500 at 60:1 (Kt 0.01825, R 0.04669):
#   ARM_KG = 2 * 9.81 * 0.25 / (60 * 0.01825) * 0.04669 = 0.209 V, times cos(angle) from horizontal.
ELEVATOR_KG = 0.105
ARM_KG = 0.209


class MyRobot(wpilib.TimedRobot):
    def robotInit(self) -> None:
        self.elevator_motor = wpilib.PWMSparkMax(2)
        self.arm_motor = wpilib.PWMSparkMax(3)

        self.elevator_enc = wpilib.Encoder(4, 5)
        self.elevator_enc.setDistancePerPulse(2 * math.pi * SPOOL_RADIUS_M * STAGES / ENCODER_CPR)  # meters
        self.arm_enc = wpilib.Encoder(6, 7)
        self.arm_enc.setDistancePerPulse(360.0 / ENCODER_CPR)  # degrees

        self.elevator_pid = PIDController(40.0, 0.0, 2.0)
        self.elevator_pid.setTolerance(0.01)
        self.arm_pid = PIDController(0.3, 0.0, 0.01)
        self.arm_pid.setTolerance(1.0)
        self.elevator_done = False
        self.arm_done = False
        self.elevator_settled = 0
        self.arm_settled = 0

    def robotPeriodic(self) -> None:
        wpilib.SmartDashboard.putNumber("Elevator m", self.elevator_enc.getDistance())
        wpilib.SmartDashboard.putNumber("Arm deg", self.arm_enc.getDistance())

    def autonomousInit(self) -> None:
        self.elevator_pid.reset()
        self.arm_pid.reset()
        self.elevator_done = self.arm_done = False
        self.elevator_settled = self.arm_settled = 0

    def autonomousPeriodic(self) -> None:
        height = self.elevator_enc.getDistance()
        angle = self.arm_enc.getDistance()
        volts = self.elevator_pid.calculate(height, TARGET_HEIGHT_M) + ELEVATOR_KG
        self.elevator_motor.setVoltage(max(-12.0, min(12.0, volts)))
        volts = self.arm_pid.calculate(angle, TARGET_ARM_DEG) + ARM_KG * math.cos(math.radians(angle))
        self.arm_motor.setVoltage(max(-12.0, min(12.0, volts)))

        # "At target" means within tolerance for 10 loops (0.2 s) in a row.
        self.elevator_settled = self.elevator_settled + 1 if self.elevator_pid.atSetpoint() else 0
        self.arm_settled = self.arm_settled + 1 if self.arm_pid.atSetpoint() else 0
        if not self.elevator_done and self.elevator_settled >= 10:
            self.elevator_done = True
            print(f"elevator: at {TARGET_HEIGHT_M:.2f}", flush=True)
        if not self.arm_done and self.arm_settled >= 10:
            self.arm_done = True
            print(f"arm: at {TARGET_ARM_DEG:.0f}", flush=True)

    def disabledInit(self) -> None:
        self.elevator_motor.set(0)
        self.arm_motor.set(0)

    def teleopPeriodic(self) -> None:
        pass


if __name__ == "__main__":
    wpilib.run(MyRobot)
