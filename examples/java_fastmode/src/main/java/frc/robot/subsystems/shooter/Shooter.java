package frc.robot.subsystems.shooter;

import static frc.robot.ScrimmageConstants.*;

import edu.wpi.first.math.MathUtil;
import edu.wpi.first.math.filter.Debouncer;
import edu.wpi.first.math.geometry.Pose2d;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.geometry.Translation2d;
import edu.wpi.first.math.kinematics.ChassisSpeeds;
import edu.wpi.first.wpilibj.DriverStation;
import edu.wpi.first.wpilibj.DriverStation.Alliance;
import edu.wpi.first.wpilibj2.command.SubsystemBase;
import org.littletonrobotics.junction.AutoLogOutput;
import org.littletonrobotics.junction.Logger;

/**
 * A two-lane hooded launcher with a two-stage feeder and an intake. It aims from {@link AimTable}:
 * wheel speed and hood angle by distance to the alliance's hub.
 *
 * <p>{@link AimTable} is generated from scrimmage's launcher model of this robot (in the repository
 * root):
 *
 * <pre>
 * scrimmage launcher aim configs/fastmode_range.toml --goal hub_blue --distances 1.5:6.0:0.25 \
 *     --java examples/java_fastmode/src/main/java/frc/robot/subsystems/shooter/AimTable.java \
 *     --java-class frc.robot.subsystems.shooter.AimTable
 * </pre>
 *
 * <p>On the move it aims at a virtual target: the hub less the robot's velocity times the ball's
 * flight time, so the ball, carrying the robot's velocity, lands in the hub.
 */
public class Shooter extends SubsystemBase {
  /** Feed only with the wheels within this fraction of the target speed. */
  public static final double WHEEL_TOLERANCE = 0.02;
  /** ... for this long (s): the measured speed lags a ball's dip, more so a Talon's ... */
  public static final double WHEEL_SETTLE = 0.06;
  /** ... the hood within this of its target angle (degrees) ... */
  public static final double HOOD_TOLERANCE = 1.0;
  /** ... and the robot facing the target within this (degrees). */
  public static final double HEADING_TOLERANCE = 2.0;

  /**
   * The hubs' centers on the field (m): the 2026 field CAD, as scrimmage's 2026 range file has
   * them.
   */
  public static final Translation2d BLUE_HUB = new Translation2d(4.625, 4.035);

  public static final Translation2d RED_HUB = new Translation2d(11.916, 4.035);

  // AimTable.ROWS columns
  private static final int DISTANCE = 0;
  private static final int WHEEL_RPS = 1;
  private static final int HOOD_DEG = 2;
  private static final int FLIGHT_S = 3;

  /**
   * A shot: where to aim, and the wheel speed and hood angle for it. {@code inRange}: the distance
   * lies within the aiming table's rows; outside them the wheel speed and hood angle are its end
   * row's, to stay ready, but the shot isn't taken.
   */
  public record Shot(
      Translation2d target,
      Rotation2d heading,
      double distance,
      double wheelRps,
      double hoodDegrees,
      boolean inRange) {}

  private final ShooterIO io;
  private final ShooterIOInputsAutoLogged inputs = new ShooterIOInputsAutoLogged();
  private double targetRps = 0.0;
  private double targetHoodDegrees = HOOD_MIN_DEGREES;
  private final Debouncer wheelSettled = new Debouncer(WHEEL_SETTLE);
  private boolean atSpeed = false;

  public Shooter(ShooterIO io) {
    this.io = io;
    setDefaultCommand(run(this::rest));
  }

  @Override
  public void periodic() {
    io.updateInputs(inputs);
    Logger.processInputs("Shooter", inputs);
    atSpeed =
        wheelSettled.calculate(
            targetRps > 0.0
                && Math.abs(inputs.wheelRps - targetRps) <= WHEEL_TOLERANCE * targetRps);
  }

  /** The alliance's hub (blue when the alliance isn't known). */
  public static Translation2d hub() {
    return DriverStation.getAlliance().orElse(Alliance.Blue) == Alliance.Red ? RED_HUB : BLUE_HUB;
  }

  /**
   * The shot from {@code pose} moving at {@code fieldVelocity} (m/s, field frame): aimed at the hub
   * less the velocity times the flight time, the flight time looked up again at the new distance
   * (twice).
   */
  public static Shot aim(Pose2d pose, ChassisSpeeds fieldVelocity, Translation2d hub) {
    Translation2d robot = pose.getTranslation();
    Translation2d target = hub;
    double[] row = lookup(robot.getDistance(target));
    for (int i = 0; i < 2; i++) {
      double t = row[FLIGHT_S];
      target =
          hub.minus(
              new Translation2d(
                  fieldVelocity.vxMetersPerSecond * t, fieldVelocity.vyMetersPerSecond * t));
      row = lookup(robot.getDistance(target));
    }
    double distance = robot.getDistance(target);
    return new Shot(
        target,
        target.minus(robot).getAngle(),
        distance,
        row[WHEEL_RPS],
        row[HOOD_DEG],
        inRange(distance));
  }

  /** Whether the aiming table covers {@code distance}: between its first and last rows. */
  public static boolean inRange(double distance) {
    double[][] rows = AimTable.ROWS;
    return distance >= rows[0][DISTANCE] && distance <= rows[rows.length - 1][DISTANCE];
  }

  /**
   * AimTable's row at {@code distance}, interpolated linearly; its end rows outside them (see
   * {@link Shot#inRange}).
   */
  static double[] lookup(double distance) {
    double[][] rows = AimTable.ROWS;
    if (distance <= rows[0][DISTANCE]) {
      return rows[0];
    }
    for (int i = 1; i < rows.length; i++) {
      if (distance <= rows[i][DISTANCE]) {
        double[] a = rows[i - 1];
        double[] b = rows[i];
        double f = (distance - a[DISTANCE]) / (b[DISTANCE] - a[DISTANCE]);
        double[] out = new double[a.length];
        for (int k = 0; k < a.length; k++) {
          out[k] = MathUtil.interpolate(a[k], b[k], f);
        }
        return out;
      }
    }
    return rows[rows.length - 1];
  }

  /** Spins the wheels up to and moves the hood to the shot's. */
  public void prepare(Shot shot) {
    targetRps = shot.wheelRps();
    targetHoodDegrees = MathUtil.clamp(shot.hoodDegrees(), HOOD_MIN_DEGREES, HOOD_MAX_DEGREES);
    io.setWheelRps(targetRps);
    io.setHoodDegrees(targetHoodDegrees);
  }

  /**
   * Lets the wheels coast and stops the feeder (the hood stays where it is, the intake as it is).
   */
  public void rest() {
    targetRps = 0.0;
    io.stopWheel();
    feed(false);
  }

  /** Runs the hopper floor and the feeder into the launcher, or stops them. */
  public void feed(boolean on) {
    io.setFloorVolts(on ? FEED_VOLTS : 0.0);
    io.setFeederVolts(on ? FEED_VOLTS : 0.0);
  }

  /** Runs the intake roller inward, or stops it. */
  public void intake(boolean on) {
    io.setIntakeVolts(on ? INTAKE_VOLTS : 0.0);
  }

  @AutoLogOutput(key = "Shooter/TargetRps")
  public double getTargetRps() {
    return targetRps;
  }

  @AutoLogOutput(key = "Shooter/TargetHoodDegrees")
  public double getTargetHoodDegrees() {
    return targetHoodDegrees;
  }

  /**
   * The wheels within {@link #WHEEL_TOLERANCE} of a non-zero target (for {@link #WHEEL_SETTLE}) and
   * the hood on its angle.
   */
  @AutoLogOutput(key = "Shooter/Ready")
  public boolean ready() {
    return atSpeed && Math.abs(inputs.hoodDegrees - targetHoodDegrees) <= HOOD_TOLERANCE;
  }

  /** A ball at the launcher's beam break: one just fed into the wheels. */
  public boolean ballEnteringLauncher() {
    return inputs.launcherBeamBroken;
  }

  /** A ball at either beam break: halfway along the feeder or at the launcher's entry. */
  @AutoLogOutput(key = "Shooter/BallAtBeams")
  public boolean ballAtBeams() {
    return inputs.feederBeamBroken || inputs.launcherBeamBroken;
  }
}
