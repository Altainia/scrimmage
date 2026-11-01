// Copyright (c) scrimmage contributors. BSD-3-Clause (the scrimmage license, see pyproject.toml).
// Written for scrimmage against Phoenix 6's public swerve API; not derived from CTRE's examples.
package frc.robot;

import com.ctre.phoenix6.configs.CANcoderConfiguration;
import com.ctre.phoenix6.configs.CurrentLimitsConfigs;
import com.ctre.phoenix6.configs.Slot0Configs;
import com.ctre.phoenix6.configs.TalonFXConfiguration;
import com.ctre.phoenix6.signals.NeutralModeValue;
import com.ctre.phoenix6.swerve.SwerveDrivetrainConstants;
import com.ctre.phoenix6.swerve.SwerveModuleConstants;
import com.ctre.phoenix6.swerve.SwerveModuleConstants.ClosedLoopOutputType;
import com.ctre.phoenix6.swerve.SwerveModuleConstants.DriveMotorArrangement;
import com.ctre.phoenix6.swerve.SwerveModuleConstants.SteerFeedbackType;
import com.ctre.phoenix6.swerve.SwerveModuleConstants.SteerMotorArrangement;
import com.ctre.phoenix6.swerve.SwerveModuleConstantsFactory;
import edu.wpi.first.math.geometry.Pose2d;
import edu.wpi.first.math.geometry.Rotation2d;

/**
 * Everything about the drivetrain hardware, in SI units (meters, rotations, volts).
 * These must agree with configs/swerve.toml in scrimmage (four stock swerve/MK4i parts on a chassis).
 */
public final class Constants {
  private Constants() {}

  /** Where scrimmage places the robot ([robot] start_pose), so odometry starts on the field. */
  public static final Pose2d START_POSE = new Pose2d(2.0, 4.035, Rotation2d.kZero);

  public static final String CAN_BUS = "rio";
  public static final int PIGEON_ID = 13;

  /** MK4i L2: rotor revolutions per wheel revolution. */
  public static final double DRIVE_GEAR_RATIO = 6.75;
  /** MK4i: rotor revolutions per module revolution (150/7). */
  public static final double STEER_GEAR_RATIO = 150.0 / 7.0;
  /** The model's modules have no bevel-gear coupling between steer and drive. */
  public static final double COUPLING_RATIO = 0.0;
  public static final double WHEEL_RADIUS = 0.0508;  // 4 in wheel
  /** Kraken X60 free speed (~100 rps) through 6.75 on a 4 in wheel, less a little for losses. */
  public static final double SPEED_AT_12V = 4.6;      // m/s

  /** Module centers from the robot center: 0.2921 m = 11.5 in each way. */
  public static final double MODULE_OFFSET = 0.2921;

  /** Steer gains: Voltage output, in module rotations (RemoteCANcoder feedback). */
  private static final Slot0Configs STEER_GAINS = new Slot0Configs()
      .withKP(100).withKI(0).withKD(0.5).withKS(0.1).withKV(2.6);
  /** Drive gains: Voltage output, in rotor rotations per second. */
  private static final Slot0Configs DRIVE_GAINS = new Slot0Configs()
      .withKP(0.1).withKI(0).withKD(0).withKS(0).withKV(0.12);

  public static final SwerveDrivetrainConstants DRIVETRAIN = new SwerveDrivetrainConstants()
      .withCANBusName(CAN_BUS)
      .withPigeon2Id(PIGEON_ID);

  private static TalonFXConfiguration brakeConfig(double statorLimitAmps) {
    var cfg = new TalonFXConfiguration()
        .withCurrentLimits(new CurrentLimitsConfigs()
            .withStatorCurrentLimit(statorLimitAmps).withStatorCurrentLimitEnable(true));
    cfg.MotorOutput.NeutralMode = NeutralModeValue.Brake;   // neutral = "brake" in configs/swerve.toml
    return cfg;
  }

  private static final SwerveModuleConstantsFactory<TalonFXConfiguration, TalonFXConfiguration, CANcoderConfiguration> MODULES =
      new SwerveModuleConstantsFactory<TalonFXConfiguration, TalonFXConfiguration, CANcoderConfiguration>()
          .withDriveMotorGearRatio(DRIVE_GEAR_RATIO)
          .withSteerMotorGearRatio(STEER_GEAR_RATIO)
          .withCouplingGearRatio(COUPLING_RATIO)
          .withWheelRadius(WHEEL_RADIUS)
          .withSteerMotorGains(STEER_GAINS)
          .withDriveMotorGains(DRIVE_GAINS)
          .withSteerMotorClosedLoopOutput(ClosedLoopOutputType.Voltage)
          .withDriveMotorClosedLoopOutput(ClosedLoopOutputType.Voltage)
          .withSlipCurrent(120)
          .withSpeedAt12Volts(SPEED_AT_12V)
          .withDriveMotorType(DriveMotorArrangement.TalonFX_Integrated)
          .withSteerMotorType(SteerMotorArrangement.TalonFX_Integrated)
          .withFeedbackSource(SteerFeedbackType.RemoteCANcoder)   // no Phoenix Pro license needed
          .withDriveMotorInitialConfigs(brakeConfig(120))
          .withSteerMotorInitialConfigs(brakeConfig(60))
          .withEncoderInitialConfigs(new CANcoderConfiguration())
          // Only used by Phoenix's own simple sim model in updateSimState(); scrimmage's physics replaces it.
          .withSteerInertia(0.04)
          .withDriveInertia(0.03)
          .withSteerFrictionVoltage(0.2)
          .withDriveFrictionVoltage(0.2);

  // Magnet offsets are 0: scrimmage's CANcoders read 0 with the wheels straight forward.
  // Inversions are a real MK4i's: the steer motor is mounted upside down, so it is inverted on every
  // module, and the right-side drive motors face the other way, so they are inverted too.
  // (configs/swerve.toml: the MK4i part's steer_invert defaults to true; fr and br set drive_invert = true.)
  private static final boolean STEER_INVERTED = true;

  private static SwerveModuleConstants<TalonFXConfiguration, TalonFXConfiguration, CANcoderConfiguration> module(
      int driveId, int steerId, int encoderId, double x, double y, boolean driveInverted) {
    // createModuleConstants(steerId, driveId, encoderId, encoderOffset, x, y,
    //                       driveMotorInverted, steerMotorInverted, encoderInverted)
    return MODULES.createModuleConstants(steerId, driveId, encoderId, 0.0, x, y, driveInverted, STEER_INVERTED, false);
  }

  public static final SwerveModuleConstants<?, ?, ?> FRONT_LEFT = module(1, 2, 9, MODULE_OFFSET, MODULE_OFFSET, false);
  public static final SwerveModuleConstants<?, ?, ?> FRONT_RIGHT = module(3, 4, 10, MODULE_OFFSET, -MODULE_OFFSET, true);
  public static final SwerveModuleConstants<?, ?, ?> BACK_LEFT = module(5, 6, 11, -MODULE_OFFSET, MODULE_OFFSET, false);
  public static final SwerveModuleConstants<?, ?, ?> BACK_RIGHT = module(7, 8, 12, -MODULE_OFFSET, -MODULE_OFFSET, true);

  /** Teleop limits. */
  public static final double MAX_SPEED = SPEED_AT_12V;        // m/s
  public static final double MAX_ANGULAR_RATE = 1.5 * Math.PI; // rad/s

  /** Autonomous: drive this far at this speed, then turn to face this way. */
  public static final double AUTO_DISTANCE = 1.0;   // m
  public static final double AUTO_SPEED = 1.0;      // m/s
  public static final double AUTO_SLOWDOWN_GAIN = 3.0;      // m/s per m still to go
  public static final double AUTO_MIN_SPEED = 0.1;          // m/s, so it always arrives
  public static final double AUTO_DISTANCE_TOLERANCE = 0.01; // m
  public static final Rotation2d AUTO_HEADING = Rotation2d.fromDegrees(90);
  public static final double HEADING_KP = 6.0;      // rad/s per rad of heading error
  // Wider than where the P-only heading loop stalls: near 2 degrees its command falls inside the Talons' neutral
  // deadband and the robot stops turning (on hardware too), so a 2-degree tolerance was sometimes never met.
  public static final double HEADING_TOLERANCE = Math.toRadians(3);
}
