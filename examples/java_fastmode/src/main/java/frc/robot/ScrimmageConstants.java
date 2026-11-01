package frc.robot;

import com.ctre.phoenix6.configs.CANcoderConfiguration;
import com.ctre.phoenix6.configs.TalonFXConfiguration;
import com.ctre.phoenix6.signals.FeedbackSensorSourceValue;
import com.ctre.phoenix6.signals.GravityTypeValue;
import com.ctre.phoenix6.signals.InvertedValue;
import com.ctre.phoenix6.signals.NeutralModeValue;
import com.ctre.phoenix6.signals.SensorDirectionValue;
import com.ctre.phoenix6.signals.StaticFeedforwardSignValue;
import com.ctre.phoenix6.swerve.SwerveModuleConstants;
import com.ctre.phoenix6.swerve.SwerveModuleConstants.ClosedLoopOutputType;
import edu.wpi.first.wpilibj.DriverStation;
import edu.wpi.first.wpilibj.RobotBase;
import scrimmage.sim.KrakenSettings;

/**
 * One set of constants for both IO layers: the Phoenix configs the real IO applies, and the
 * scrimmage fast-mode {@link KrakenSettings} built from those same configs.
 *
 * <p>The swerve constants come from {@link frc.robot.generated.TunerConstants}; the elevator's and
 * the arm's are here. Building a Phoenix config object loads no Phoenix device: fast mode creates
 * none.
 */
public final class ScrimmageConstants {
  private ScrimmageConstants() {}

  // ---- Elevator: two Kraken X60s (the second a follower, opposed) on a 9:1 gearbox to a 22 mm
  // spool. Mechanism units are spool rotations.
  public static final int ELEVATOR_LEAD_ID = 8;
  public static final int ELEVATOR_FOLLOWER_ID = 9;
  public static final double ELEVATOR_GEARING = 9.0; // motor rotations per spool rotation
  public static final double ELEVATOR_SPOOL_RADIUS = 0.022; // m
  public static final double ELEVATOR_METERS_PER_ROTATION = 2 * Math.PI * ELEVATOR_SPOOL_RADIUS;
  public static final double ELEVATOR_TRAVEL = 1.0; // m
  /** The height the autonomous routine raises the elevator to (m). */
  public static final double ELEVATOR_AUTO_HEIGHT = 0.75;

  public static TalonFXConfiguration elevatorConfig() {
    var config = new TalonFXConfiguration();
    config.MotorOutput.Inverted = InvertedValue.CounterClockwise_Positive;
    config.MotorOutput.NeutralMode = NeutralModeValue.Brake;
    config.Feedback.SensorToMechanismRatio = ELEVATOR_GEARING;
    config.Slot0.kP = 8.0; // V per spool rotation of error
    config.Slot0.kD = 0.1;
    config.Slot0.kS = 0.1;
    config.Slot0.kV = 1.08; // 12 V / 11.1 spool rps (6000 rpm / 9)
    config.Slot0.kA = 0.01;
    config.Slot0.kG = 0.20; // the carriage plus the arm on it (2.5 kg: +0.05 V)
    config.Slot0.GravityType = GravityTypeValue.Elevator_Static;
    config.Slot0.StaticFeedforwardSign = StaticFeedforwardSignValue.UseVelocitySign;
    config.MotionMagic.MotionMagicCruiseVelocity = 6.0; // spool rps: 0.83 m/s
    config.MotionMagic.MotionMagicAcceleration = 24.0; // spool rps/s
    config.MotionMagic.MotionMagicJerk = 240.0; // spool rps/s/s: an S-curve profile
    // Real Talons ship with current limits enabled; set them on purpose.
    config.CurrentLimits.StatorCurrentLimit = 80.0;
    config.CurrentLimits.StatorCurrentLimitEnable = true;
    config.CurrentLimits.SupplyCurrentLimit = 40.0;
    config.CurrentLimits.SupplyCurrentLimitEnable = true;
    config.SoftwareLimitSwitch.ForwardSoftLimitThreshold =
        (ELEVATOR_TRAVEL - 0.02) / ELEVATOR_METERS_PER_ROTATION;
    config.SoftwareLimitSwitch.ForwardSoftLimitEnable = true;
    config.SoftwareLimitSwitch.ReverseSoftLimitThreshold = 0.0;
    config.SoftwareLimitSwitch.ReverseSoftLimitEnable = true;
    return config;
  }

  // ---- Arm: one Kraken X60 through 60:1 to a 0.5 m, 2 kg arm on the elevator carriage. Mechanism
  // units are arm rotations, 0 = horizontal forward (Arm/Cosine needs that), positive raises the
  // tip.
  public static final int ARM_ID = 10;
  public static final double ARM_GEARING = 60.0; // motor rotations per arm rotation
  /**
   * The angle the arm is at when the robot program starts (degrees): it starts horizontal in
   * scrimmage. A real robot would start resting on a hard stop at a known angle.
   */
  public static final double ARM_START_DEGREES = 0.0;
  /** The angle the autonomous routine raises the arm to (degrees). */
  public static final double ARM_AUTO_ANGLE = 45.0;

  public static TalonFXConfiguration armConfig() {
    var config = new TalonFXConfiguration();
    config.MotorOutput.Inverted = InvertedValue.CounterClockwise_Positive;
    config.MotorOutput.NeutralMode = NeutralModeValue.Brake;
    config.Feedback.SensorToMechanismRatio = ARM_GEARING;
    config.Slot0.kP = 100.0; // V per arm rotation of error: 0.28 V per degree
    config.Slot0.kD = 2.0;
    config.Slot0.kS = 0.1;
    config.Slot0.kV = 7.2; // 12 V / 1.67 arm rps (6000 rpm / 60)
    config.Slot0.kA = 0.03;
    config.Slot0.kG = 0.14; // holds 2 kg at 0.25 m (4.9 N*m) horizontal: 4.2 A through 60:1
    config.Slot0.GravityType = GravityTypeValue.Arm_Cosine;
    // The arm's center of gravity lies along it, so no offset: an L-shaped arm would set the angle
    // from its zero to its center of gravity here (rotations, -0.25 to 0.25).
    config.Slot0.GravityArmPositionOffset = 0.0;
    config.Slot0.StaticFeedforwardSign = StaticFeedforwardSignValue.UseVelocitySign;
    config.MotionMagic.MotionMagicCruiseVelocity = 0.5; // arm rps: 180 deg/s
    config.MotionMagic.MotionMagicAcceleration = 2.0; // arm rps/s
    config.MotionMagic.MotionMagicJerk = 0.0; // a trapezoidal profile
    config.CurrentLimits.StatorCurrentLimit = 60.0;
    config.CurrentLimits.StatorCurrentLimitEnable = true;
    config.CurrentLimits.SupplyCurrentLimit = 40.0;
    config.CurrentLimits.SupplyCurrentLimitEnable = true;
    config.SoftwareLimitSwitch.ForwardSoftLimitThreshold = 100.0 / 360.0;
    config.SoftwareLimitSwitch.ForwardSoftLimitEnable = true;
    config.SoftwareLimitSwitch.ReverseSoftLimitThreshold = -40.0 / 360.0;
    config.SoftwareLimitSwitch.ReverseSoftLimitEnable = true;
    return config;
  }

  // ---- Driving over the 2026 BUMP (scrimmage's 2026-bump range). The speeds are the limits that
  // `scrimmage terrain sweep configs/fastmode_bump.toml --element bump` gives this robot (on 8 cm
  // bumpers): the fastest it takes the bump with no more than 5 cm of air and 30 degrees of tilt,
  // never tipped or stranded. See docs/terrain.md.
  /**
   * The bump autos drive this far straight ahead (m): from 1.5 m before the bump to 1.7 m past it.
   */
  public static final double BUMP_DRIVE_DISTANCE = 4.0;
  /** The speed over the bump with the elevator down (m/s). */
  public static final double BUMP_SPEED = 2.0;
  /** With the elevator up the robot is top-heavy: it pitches further off the crest (m/s). */
  public static final double BUMP_SPEED_ELEVATOR_UP = 1.0;
  /** The height the elevator-up bump auto raises the elevator to before the bump (m). */
  public static final double ELEVATOR_BUMP_HEIGHT = 0.9;
  /**
   * The bump autos speed up and slow down no faster than this (m/s^2), so the wheels don't slip.
   */
  public static final double BUMP_ACCELERATION = 3.0;

  /**
   * The bump autos' speed: {@code speed}, or in simulation the environment variable
   * SCRIMMAGE_BUMP_SPEED (m/s) if set, so a scrimmage scenario can try another speed (robot_env).
   */
  public static double bumpSpeed(double speed) {
    String override = System.getenv("SCRIMMAGE_BUMP_SPEED");
    if (!RobotBase.isSimulation() || override == null) {
      return speed;
    }
    try {
      return Double.parseDouble(override);
    } catch (NumberFormatException e) {
      DriverStation.reportError(
          "SCRIMMAGE_BUMP_SPEED=" + override + ": not a number; using " + speed, false);
      return speed;
    }
  }

  // ---- Shooter (a made-up two-lane hooded launcher, for scrimmage's test range): two Kraken X60s
  // direct on a 4 in. wheel shaft, a hood on its own Kraken through 50:1, a two-stage feeder (the
  // hopper floor, then the feeder) and an intake roller, each a Kraken through 5:1 to a 50 mm
  // roller. Two beam breaks: halfway along the feeder and at the launcher's entry.
  public static final int SHOOTER_LEAD_ID = 11;
  public static final int SHOOTER_FOLLOWER_ID = 12;
  public static final int HOOD_ID = 13;
  public static final int FLOOR_ID = 14;
  public static final int FEEDER_ID = 15;
  public static final int INTAKE_ID = 16;
  public static final int FEEDER_BEAM_DIO = 0;
  public static final int LAUNCHER_BEAM_DIO = 1;
  public static final double SHOOTER_GEARING = 1.0; // motor rotations per wheel rotation
  public static final double HOOD_GEARING = 50.0; // motor rotations per hood rotation
  /**
   * The hood's exit angles at its hard stops (degrees above horizontal). The hood starts on the low
   * stop, where its position reads 0: exit angle = HOOD_MIN_DEGREES + hood rotations * 360.
   */
  public static final double HOOD_MIN_DEGREES = 40.0;

  public static final double HOOD_MAX_DEGREES = 80.0;
  public static final double ROLLER_GEARING = 5.0; // floor, feeder and intake
  /** Volts on the hopper floor and the feeder while feeding (about 2 m/s at the rollers). */
  public static final double FEED_VOLTS = 8.0;
  /** Volts on the intake roller while intaking. */
  public static final double INTAKE_VOLTS = 10.0;

  /** The shooter wheels: velocity closed loop, mechanism units wheel rotations. */
  public static TalonFXConfiguration shooterConfig() {
    var config = new TalonFXConfiguration();
    config.MotorOutput.Inverted = InvertedValue.CounterClockwise_Positive;
    config.MotorOutput.NeutralMode = NeutralModeValue.Coast;
    config.Feedback.SensorToMechanismRatio = SHOOTER_GEARING;
    config.Slot0.kP = 0.5; // V per wheel rps of error
    config.Slot0.kS = 0.15;
    config.Slot0.kV = 0.12; // 12 V / 100 rps (6000 rpm, direct)
    config.CurrentLimits.StatorCurrentLimit = 100.0;
    config.CurrentLimits.StatorCurrentLimitEnable = true;
    config.CurrentLimits.SupplyCurrentLimit = 50.0;
    config.CurrentLimits.SupplyCurrentLimitEnable = true;
    return config;
  }

  /** The hood: Motion Magic, mechanism units hood rotations from the low stop. */
  public static TalonFXConfiguration hoodConfig() {
    var config = new TalonFXConfiguration();
    config.MotorOutput.Inverted = InvertedValue.CounterClockwise_Positive;
    config.MotorOutput.NeutralMode = NeutralModeValue.Brake;
    config.Feedback.SensorToMechanismRatio = HOOD_GEARING;
    config.Slot0.kP = 120.0; // V per hood rotation of error: 0.33 V per degree
    config.Slot0.kD = 1.0;
    config.Slot0.kS = 0.1;
    config.Slot0.kV = 6.0; // 12 V / 2 hood rps (6000 rpm / 50)
    config.MotionMagic.MotionMagicCruiseVelocity = 0.5; // hood rps: 180 deg/s
    config.MotionMagic.MotionMagicAcceleration = 4.0; // hood rps/s
    config.CurrentLimits.StatorCurrentLimit = 40.0;
    config.CurrentLimits.StatorCurrentLimitEnable = true;
    config.CurrentLimits.SupplyCurrentLimit = 30.0;
    config.CurrentLimits.SupplyCurrentLimitEnable = true;
    config.SoftwareLimitSwitch.ForwardSoftLimitThreshold =
        (HOOD_MAX_DEGREES - HOOD_MIN_DEGREES) / 360.0;
    config.SoftwareLimitSwitch.ForwardSoftLimitEnable = true;
    config.SoftwareLimitSwitch.ReverseSoftLimitThreshold = 0.0;
    config.SoftwareLimitSwitch.ReverseSoftLimitEnable = true;
    return config;
  }

  /** The floor, feeder and intake rollers: open loop (volts), mechanism units roller rotations. */
  public static TalonFXConfiguration rollerConfig() {
    var config = new TalonFXConfiguration();
    config.MotorOutput.Inverted = InvertedValue.CounterClockwise_Positive;
    config.MotorOutput.NeutralMode = NeutralModeValue.Brake;
    config.Feedback.SensorToMechanismRatio = ROLLER_GEARING;
    config.CurrentLimits.StatorCurrentLimit = 40.0;
    config.CurrentLimits.StatorCurrentLimitEnable = true;
    config.CurrentLimits.SupplyCurrentLimit = 30.0;
    config.CurrentLimits.SupplyCurrentLimitEnable = true;
    return config;
  }

  // ---- Swerve modules (moved here from the template's ModuleIOTalonFX, unchanged) ----

  /**
   * The drive motor's config. As in the template, this fills in and returns the module constants'
   * DriveMotorInitialConfigs (one object shared by the four modules): use it before building the
   * next module's.
   */
  public static TalonFXConfiguration driveConfig(
      SwerveModuleConstants<TalonFXConfiguration, TalonFXConfiguration, CANcoderConfiguration>
          constants) {
    var driveConfig = constants.DriveMotorInitialConfigs;
    driveConfig.MotorOutput.NeutralMode = NeutralModeValue.Brake;
    driveConfig.Slot0 = constants.DriveMotorGains;
    driveConfig.Feedback.SensorToMechanismRatio = constants.DriveMotorGearRatio;
    driveConfig.TorqueCurrent.PeakForwardTorqueCurrent = constants.SlipCurrent;
    driveConfig.TorqueCurrent.PeakReverseTorqueCurrent = -constants.SlipCurrent;
    driveConfig.CurrentLimits.StatorCurrentLimit = constants.SlipCurrent;
    driveConfig.CurrentLimits.StatorCurrentLimitEnable = true;
    driveConfig.MotorOutput.Inverted =
        constants.DriveMotorInverted
            ? InvertedValue.Clockwise_Positive
            : InvertedValue.CounterClockwise_Positive;
    return driveConfig;
  }

  /** The turn (steer) motor's config: closed loop on the module's CANcoder. */
  public static TalonFXConfiguration turnConfig(
      SwerveModuleConstants<TalonFXConfiguration, TalonFXConfiguration, CANcoderConfiguration>
          constants) {
    var turnConfig = new TalonFXConfiguration();
    turnConfig.MotorOutput.NeutralMode = NeutralModeValue.Brake;
    turnConfig.Slot0 = constants.SteerMotorGains;
    turnConfig.Feedback.FeedbackRemoteSensorID = constants.EncoderId;
    turnConfig.Feedback.FeedbackSensorSource =
        switch (constants.FeedbackSource) {
          case RemoteCANcoder -> FeedbackSensorSourceValue.RemoteCANcoder;
          case FusedCANcoder -> FeedbackSensorSourceValue.FusedCANcoder;
          case SyncCANcoder -> FeedbackSensorSourceValue.SyncCANcoder;
          default -> throw new RuntimeException(
              "You have selected a turn feedback source that is not supported by the default implementation of ModuleIOTalonFX. Please check the AdvantageKit documentation for more information on alternative configurations: https://docs.advantagekit.org/getting-started/template-projects/talonfx-swerve-template#custom-module-implementations");
        };
    turnConfig.Feedback.RotorToSensorRatio = constants.SteerMotorGearRatio;
    turnConfig.MotionMagic.MotionMagicCruiseVelocity = 100.0 / constants.SteerMotorGearRatio;
    turnConfig.MotionMagic.MotionMagicAcceleration =
        turnConfig.MotionMagic.MotionMagicCruiseVelocity / 0.100;
    turnConfig.MotionMagic.MotionMagicExpo_kV = 0.12 * constants.SteerMotorGearRatio;
    turnConfig.MotionMagic.MotionMagicExpo_kA = 0.1;
    turnConfig.ClosedLoopGeneral.ContinuousWrap = true;
    turnConfig.MotorOutput.Inverted =
        constants.SteerMotorInverted
            ? InvertedValue.Clockwise_Positive
            : InvertedValue.CounterClockwise_Positive;
    return turnConfig;
  }

  /** The CANcoder's config (as for the drive motor, this fills in the shared initial configs). */
  public static CANcoderConfiguration encoderConfig(
      SwerveModuleConstants<TalonFXConfiguration, TalonFXConfiguration, CANcoderConfiguration>
          constants) {
    CANcoderConfiguration cancoderConfig = constants.EncoderInitialConfigs;
    cancoderConfig.MagnetSensor.MagnetOffset = constants.EncoderOffset;
    cancoderConfig.MagnetSensor.SensorDirection =
        constants.EncoderInverted
            ? SensorDirectionValue.Clockwise_Positive
            : SensorDirectionValue.CounterClockwise_Positive;
    return cancoderConfig;
  }

  /**
   * Fast mode models only voltage closed-loop output (TorqueCurrentFOC is a Phoenix Pro feature).
   */
  public static void requireVoltageOutput(ClosedLoopOutputType type, String which) {
    if (type != ClosedLoopOutputType.Voltage) {
      throw new IllegalArgumentException(
          which + " closed-loop output is " + type + "; scrimmage fast mode supports Voltage only");
    }
  }

  // ---- Phoenix config -> fast-mode settings ----

  /**
   * The fast-mode settings matching a Talon FX config: each field means what the config's field of
   * the same name means. Fast mode has no equivalent for the rest (Motion Magic Expo,
   * torque-current limits, supply current lower limit, feedback source: a remote CANcoder is passed
   * to the ScrimmageKraken instead).
   */
  public static KrakenSettings krakenSettings(TalonFXConfiguration c) {
    return new KrakenSettings()
        .withKP(c.Slot0.kP)
        .withKI(c.Slot0.kI)
        .withKD(c.Slot0.kD)
        .withKS(c.Slot0.kS)
        .withKV(c.Slot0.kV)
        .withKA(c.Slot0.kA)
        .withKG(c.Slot0.kG)
        .withGravityType(
            c.Slot0.GravityType == GravityTypeValue.Arm_Cosine
                ? KrakenSettings.GravityType.ARM_COSINE
                : KrakenSettings.GravityType.ELEVATOR_STATIC)
        .withGravityArmPositionOffset(c.Slot0.GravityArmPositionOffset)
        .withStaticFeedforwardSign(
            c.Slot0.StaticFeedforwardSign == StaticFeedforwardSignValue.UseClosedLoopSign
                ? KrakenSettings.StaticFeedforwardSign.CLOSED_LOOP_SIGN
                : KrakenSettings.StaticFeedforwardSign.VELOCITY_SIGN)
        .withContinuousWrap(c.ClosedLoopGeneral.ContinuousWrap)
        .withMotionMagicCruiseVelocity(c.MotionMagic.MotionMagicCruiseVelocity)
        .withMotionMagicAcceleration(c.MotionMagic.MotionMagicAcceleration)
        .withMotionMagicJerk(c.MotionMagic.MotionMagicJerk)
        .withStatorCurrentLimit(c.CurrentLimits.StatorCurrentLimit)
        .withStatorCurrentLimitEnable(c.CurrentLimits.StatorCurrentLimitEnable)
        .withSupplyCurrentLimit(c.CurrentLimits.SupplyCurrentLimit)
        .withSupplyCurrentLimitEnable(c.CurrentLimits.SupplyCurrentLimitEnable)
        .withForwardSoftLimitThreshold(c.SoftwareLimitSwitch.ForwardSoftLimitThreshold)
        .withForwardSoftLimitEnable(c.SoftwareLimitSwitch.ForwardSoftLimitEnable)
        .withReverseSoftLimitThreshold(c.SoftwareLimitSwitch.ReverseSoftLimitThreshold)
        .withReverseSoftLimitEnable(c.SoftwareLimitSwitch.ReverseSoftLimitEnable)
        .withPeakForwardVoltage(c.Voltage.PeakForwardVoltage)
        .withPeakReverseVoltage(c.Voltage.PeakReverseVoltage)
        .withPeakForwardDutyCycle(c.MotorOutput.PeakForwardDutyCycle)
        .withPeakReverseDutyCycle(c.MotorOutput.PeakReverseDutyCycle)
        .withInverted(c.MotorOutput.Inverted == InvertedValue.Clockwise_Positive)
        .withBrake(c.MotorOutput.NeutralMode == NeutralModeValue.Brake)
        .withSensorToMechanismRatio(c.Feedback.SensorToMechanismRatio)
        .withRotorToSensorRatio(c.Feedback.RotorToSensorRatio);
  }
}
