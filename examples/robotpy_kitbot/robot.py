"""A plain WPILib differential-drive robot: nothing in here knows it's being simulated.

Hardware (same layout as WPILib's stock drive examples):
  PWM 0 / 1        left / right motor controllers (right side inverted)
  DIO 0,1 / 2,3    left / right quadrature encoders on the wheel shafts, 2048 counts per rev
  SPI              ADXRS450 gyro

Autonomous: drive 2 m forward, turn 90 degrees left, drive 1 m forward, using only the encoders
and gyro for feedback. Teleop: arcade drive on an Xbox controller on port 0.
"""

import math

import wpilib
import wpilib.drive
from wpimath.geometry import Pose2d, Rotation2d
from wpimath.kinematics import DifferentialDriveOdometry

WHEEL_RADIUS_M = 0.0762
ENCODER_CPR = 2048
START_POSE = Pose2d(2.0, 4.035, Rotation2d(0))


class MyRobot(wpilib.TimedRobot):
    def robotInit(self) -> None:
        self.left = wpilib.PWMSparkMax(0)
        self.right = wpilib.PWMSparkMax(1)
        self.right.setInverted(True)
        self.drive = wpilib.drive.DifferentialDrive(self.left, self.right)

        self.left_enc = wpilib.Encoder(0, 1)
        self.right_enc = wpilib.Encoder(2, 3, True)  # mirrored side
        for enc in (self.left_enc, self.right_enc):
            enc.setDistancePerPulse(2 * math.pi * WHEEL_RADIUS_M / ENCODER_CPR)

        self.gyro = wpilib.ADXRS450_Gyro()
        self.stick = wpilib.XboxController(0)

        self.odometry = DifferentialDriveOdometry(self.gyro.getRotation2d(), 0.0, 0.0, START_POSE)
        self.field = wpilib.Field2d()
        wpilib.SmartDashboard.putData("Field", self.field)

    def robotPeriodic(self) -> None:
        pose = self.odometry.update(self.gyro.getRotation2d(),
                                    self.left_enc.getDistance(), self.right_enc.getDistance())
        self.field.setRobotPose(pose)
        wpilib.SmartDashboard.putNumber("Gyro deg", self.gyro.getAngle())
        wpilib.SmartDashboard.putNumber("Left m", self.left_enc.getDistance())
        wpilib.SmartDashboard.putNumber("Right m", self.right_enc.getDistance())
        wpilib.SmartDashboard.putNumber("Battery V", wpilib.RobotController.getBatteryVoltage())

    # ------------------------------------------------------------------ autonomous
    def autonomousInit(self) -> None:
        self.left_enc.reset()
        self.right_enc.reset()
        self.gyro.reset()
        self.odometry.resetPosition(self.gyro.getRotation2d(), 0.0, 0.0, START_POSE)
        self.steps = [("drive", 2.0, 0.0), ("turn", None, 90.0), ("drive", 1.0, 90.0), ("stop", None, 90.0)]
        self.step = 0
        self._start_step()

    def _start_step(self) -> None:
        self.step_start_dist = self._avg_distance()
        self.settled = 0
        kind, dist, hdg = self.steps[self.step]
        pose = self.odometry.getPose()
        print(f"[auto t={wpilib.Timer.getFPGATimestamp():6.2f}] step {self.step}: {kind} "
              f"dist={dist} heading={hdg}  odometry=({pose.X():.3f}, {pose.Y():.3f}, {pose.rotation().degrees():.1f})",
              flush=True)

    def _avg_distance(self) -> float:
        return (self.left_enc.getDistance() + self.right_enc.getDistance()) / 2

    def _heading(self) -> float:
        return -self.gyro.getAngle()  # CCW positive, like field coordinates

    def autonomousPeriodic(self) -> None:
        kind, dist, hdg = self.steps[self.step]
        heading_err = hdg - self._heading()
        if kind == "drive":
            remaining = dist - (self._avg_distance() - self.step_start_dist)
            speed = max(-0.7, min(0.7, 1.2 * remaining))
            if abs(speed) < 0.12:
                speed = math.copysign(0.12, speed)
            self.drive.arcadeDrive(speed, 0.03 * heading_err, False)
            done = abs(remaining) < 0.02
        elif kind == "turn":
            rot = max(-0.6, min(0.6, 0.02 * heading_err))
            if abs(rot) < 0.25:
                rot = math.copysign(0.25, rot)  # overcome skid-steer scrub
            self.drive.arcadeDrive(0.0, rot, False)
            done = abs(heading_err) < 1.5
        else:
            self.drive.arcadeDrive(0.0, 0.0)
            done = False

        self.settled = self.settled + 1 if done else 0
        if self.settled >= 3 and self.step < len(self.steps) - 1:
            self.step += 1
            self._start_step()

    # ------------------------------------------------------------------ teleop
    def teleopPeriodic(self) -> None:
        self.drive.arcadeDrive(-self.stick.getLeftY(), -self.stick.getRightX())


if __name__ == "__main__":
    wpilib.run(MyRobot)
