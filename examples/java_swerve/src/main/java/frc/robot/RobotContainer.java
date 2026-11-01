// Copyright (c) scrimmage contributors. BSD-3-Clause (the scrimmage license, see pyproject.toml).
// Written for scrimmage against Phoenix 6's public swerve API; not derived from CTRE's examples.
package frc.robot;

import com.ctre.phoenix6.swerve.SwerveModule.DriveRequestType;
import com.ctre.phoenix6.swerve.SwerveModule.SteerRequestType;
import com.ctre.phoenix6.swerve.SwerveRequest;
import com.ctre.phoenix6.swerve.SwerveRequest.ForwardPerspectiveValue;
import edu.wpi.first.math.MathUtil;
import edu.wpi.first.math.geometry.Pose2d;
import edu.wpi.first.wpilibj.Joystick;
import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.Commands;

/** Teleop: field-centric driving from joystick 0. Autonomous: 1 m forward, then face 90 degrees. */
public class RobotContainer {
  private final Drivetrain drivetrain = new Drivetrain();
  private final Joystick joystick = new Joystick(0);

  private final SwerveRequest.FieldCentric teleopDrive = new SwerveRequest.FieldCentric()
      .withDeadband(Constants.MAX_SPEED * 0.1)
      .withRotationalDeadband(Constants.MAX_ANGULAR_RATE * 0.1)
      .withDriveRequestType(DriveRequestType.OpenLoopVoltage);

  // Autonomous requests are relative to the blue alliance wall, whatever the operator perspective.
  private final SwerveRequest.FieldCentric autoDrive = new SwerveRequest.FieldCentric()
      .withDriveRequestType(DriveRequestType.Velocity)
      .withSteerRequestType(SteerRequestType.Position)
      .withForwardPerspective(ForwardPerspectiveValue.BlueAlliance);
  private final SwerveRequest.FieldCentricFacingAngle autoFace = new SwerveRequest.FieldCentricFacingAngle()
      .withDriveRequestType(DriveRequestType.Velocity)
      .withSteerRequestType(SteerRequestType.Position)
      .withForwardPerspective(ForwardPerspectiveValue.BlueAlliance)
      .withHeadingPID(Constants.HEADING_KP, 0, 0)
      .withMaxAbsRotationalRate(Math.PI);
  private final SwerveRequest.Idle idle = new SwerveRequest.Idle();

  public RobotContainer() {
    autoFace.HeadingController.enableContinuousInput(-Math.PI, Math.PI);
    // Stick forward (negative Y) drives +x, stick left (negative X) drives +y, right stick left turns CCW.
    drivetrain.setDefaultCommand(drivetrain.applyRequest(() -> teleopDrive
        .withVelocityX(-joystick.getRawAxis(1) * Constants.MAX_SPEED)
        .withVelocityY(-joystick.getRawAxis(0) * Constants.MAX_SPEED)
        .withRotationalRate(-joystick.getRawAxis(4) * Constants.MAX_ANGULAR_RATE)));
  }

  public Command getAutonomousCommand() {
    Pose2d[] start = new Pose2d[1];
    return Commands.sequence(
        Commands.runOnce(() -> start[0] = drivetrain.getState().Pose),
        // Slow down over the last part, so the robot stops on the mark instead of coasting past it.
        drivetrain.applyRequest(() -> autoDrive
                .withVelocityX(MathUtil.clamp(Constants.AUTO_SLOWDOWN_GAIN * remaining(start[0]),
                                              Constants.AUTO_MIN_SPEED, Constants.AUTO_SPEED))
                .withVelocityY(0).withRotationalRate(0))
            .until(() -> remaining(start[0]) <= Constants.AUTO_DISTANCE_TOLERANCE),
        drivetrain.applyRequest(() -> autoFace.withVelocityX(0).withVelocityY(0).withTargetDirection(Constants.AUTO_HEADING))
            .until(this::facingTarget),
        drivetrain.applyRequest(() -> idle).withTimeout(0.5),
        Commands.runOnce(() -> {
          Pose2d p = drivetrain.getState().Pose;
          System.out.printf("auto: done x=%.3f y=%.3f heading=%.1f%n", p.getX(), p.getY(), p.getRotation().getDegrees());
        }),
        drivetrain.applyRequest(() -> idle));
  }

  /** How much farther than the auto distance odometry says there is to go from {@code start}. */
  private double remaining(Pose2d start) {
    return Constants.AUTO_DISTANCE - drivetrain.getState().Pose.getTranslation().getDistance(start.getTranslation());
  }

  private boolean facingTarget() {
    var state = drivetrain.getState();
    double error = state.Pose.getRotation().minus(Constants.AUTO_HEADING).getRadians();
    return Math.abs(error) < Constants.HEADING_TOLERANCE && Math.abs(state.Speeds.omegaRadiansPerSecond) < 0.1;
  }
}
