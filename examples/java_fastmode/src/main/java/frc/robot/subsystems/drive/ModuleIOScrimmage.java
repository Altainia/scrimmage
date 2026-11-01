package frc.robot.subsystems.drive;

import com.ctre.phoenix6.configs.CANcoderConfiguration;
import com.ctre.phoenix6.configs.TalonFXConfiguration;
import com.ctre.phoenix6.signals.SensorDirectionValue;
import com.ctre.phoenix6.swerve.SwerveModuleConstants;
import com.ctre.phoenix6.swerve.SwerveModuleConstants.SteerFeedbackType;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.util.Units;
import edu.wpi.first.wpilibj.Timer;
import frc.robot.ScrimmageConstants;
import scrimmage.sim.ControlRequest;
import scrimmage.sim.ScrimmageCANcoder;
import scrimmage.sim.ScrimmageKraken;

/**
 * Module IO implementation for scrimmage's fast mode: a simulated drive Kraken, turn Kraken and
 * CANcoder (WPILib only, no Phoenix devices), configured from the same Phoenix configs as {@link
 * ModuleIOTalonFX} (see {@link ScrimmageConstants}). scrimmage's physics moves them; they run
 * Talon-style control on the robot program's simulated clock.
 *
 * <p>The devices are named {@code <name>_drive}, {@code <name>_steer} and {@code <name>_encoder}:
 * the scrimmage config binds them as {@code "SimMotor:fl_drive"} and so on.
 */
public class ModuleIOScrimmage implements ModuleIO {
  private final ScrimmageKraken driveKraken;
  private final ScrimmageKraken turnKraken;
  private final ScrimmageCANcoder cancoder;

  public ModuleIOScrimmage(
      String name,
      SwerveModuleConstants<TalonFXConfiguration, TalonFXConfiguration, CANcoderConfiguration>
          constants) {
    ScrimmageConstants.requireVoltageOutput(constants.DriveMotorClosedLoopOutput, "drive");
    ScrimmageConstants.requireVoltageOutput(constants.SteerMotorClosedLoopOutput, "steer");
    driveKraken =
        new ScrimmageKraken(
            name + "_drive",
            ScrimmageConstants.krakenSettings(ScrimmageConstants.driveConfig(constants)));
    turnKraken =
        new ScrimmageKraken(
            name + "_steer",
            ScrimmageConstants.krakenSettings(ScrimmageConstants.turnConfig(constants)));
    var encoderConfig = ScrimmageConstants.encoderConfig(constants);
    cancoder =
        new ScrimmageCANcoder(
            name + "_encoder",
            encoderConfig.MagnetSensor.MagnetOffset,
            encoderConfig.MagnetSensor.SensorDirection == SensorDirectionValue.Clockwise_Positive);
    // RemoteCANcoder takes velocity from the CANcoder too; Fused (and Sync) from the rotor
    turnKraken.useRemoteEncoder(
        cancoder, constants.FeedbackSource != SteerFeedbackType.RemoteCANcoder);
    driveKraken.setPosition(0.0);
  }

  @Override
  public void updateInputs(ModuleIOInputs inputs) {
    inputs.driveConnected = true;
    inputs.drivePositionRad = Units.rotationsToRadians(driveKraken.getPosition());
    inputs.driveVelocityRadPerSec = Units.rotationsToRadians(driveKraken.getVelocity());
    inputs.driveAppliedVolts = driveKraken.getMotorVoltage();
    inputs.driveCurrentAmps = driveKraken.getStatorCurrent();

    inputs.turnConnected = true;
    inputs.turnEncoderConnected = true;
    inputs.turnAbsolutePosition = Rotation2d.fromRotations(cancoder.getAbsolutePosition());
    inputs.turnPosition = Rotation2d.fromRotations(turnKraken.getPosition());
    inputs.turnVelocityRadPerSec = Units.rotationsToRadians(turnKraken.getVelocity());
    inputs.turnAppliedVolts = turnKraken.getMotorVoltage();
    inputs.turnCurrentAmps = turnKraken.getStatorCurrent();

    // One odometry sample per robot loop, on the simulated clock (a fast-mode run repeats exactly;
    // the Phoenix odometry thread runs on the wall clock)
    inputs.odometryTimestamps = new double[] {Timer.getFPGATimestamp()};
    inputs.odometryDrivePositionsRad = new double[] {inputs.drivePositionRad};
    inputs.odometryTurnPositions = new Rotation2d[] {inputs.turnPosition};
  }

  @Override
  public void setDriveOpenLoop(double output) {
    driveKraken.setControl(ControlRequest.voltage(output));
  }

  @Override
  public void setTurnOpenLoop(double output) {
    turnKraken.setControl(ControlRequest.voltage(output));
  }

  @Override
  public void setDriveVelocity(double velocityRadPerSec) {
    driveKraken.setControl(ControlRequest.velocity(Units.radiansToRotations(velocityRadPerSec)));
  }

  @Override
  public void setTurnPosition(Rotation2d rotation) {
    turnKraken.setControl(ControlRequest.position(rotation.getRotations()));
  }
}
