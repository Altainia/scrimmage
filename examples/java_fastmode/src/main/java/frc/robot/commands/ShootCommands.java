package frc.robot.commands;

import com.pathplanner.lib.controllers.PPHolonomicDriveController;
import edu.wpi.first.math.MathUtil;
import edu.wpi.first.math.controller.PIDController;
import edu.wpi.first.math.filter.SlewRateLimiter;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.kinematics.ChassisSpeeds;
import edu.wpi.first.wpilibj.Timer;
import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.Commands;
import frc.robot.subsystems.drive.Drive;
import frc.robot.subsystems.shooter.Shooter;
import frc.robot.subsystems.shooter.Shooter.Shot;
import org.littletonrobotics.junction.Logger;

/**
 * Shooting: the robot turns its drive to face the (virtual) target, the shooter spins up and sets
 * its hood from the aiming table, and the feeder runs only while the wheels, the hood and the
 * heading are all on target.
 */
public final class ShootCommands {
  private ShootCommands() {}

  private static final double HEADING_KP = 6.0; // rad/s per rad of heading error
  private static final double HEADING_KD = 0.2;
  private static final double MAX_TURN_RATE = 6.0; // rad/s
  /**
   * How fast the turn rate may change (rad/s^2): the robot swings round smoothly instead of jerking
   * to full turn rate, which would slip the wheels and draw every drive motor's current limit at
   * once.
   */
  private static final double MAX_TURN_ACCEL = 12.0;
  /**
   * The robot counts itself empty once the beam breaks have seen no ball for this long while
   * feeding (s): longer than a ball takes from the hopper past the feeder's beam.
   */
  public static final double EMPTY_AFTER = 1.0;
  /**
   * After a ball enters the launcher (its beam break), feeding pauses this long (s) so the wheels
   * get back to speed before the next: their measured speed lags the dip a ball makes (a Talon's
   * velocity signal more than a fast-mode one), and the next ball is only a ball's length behind.
   */
  public static final double RECOVERY = 0.2;

  /**
   * Aims, turns and fires, as a part of the commands below. Each call to {@link #execute} returns
   * the turn rate that faces the target; it feeds when everything is on target.
   */
  private static final class Aimer {
    private final Drive drive;
    private final Shooter shooter;
    private final PIDController heading = new PIDController(HEADING_KP, 0.0, HEADING_KD);
    private Rotation2d lastTarget = null;
    private double lastTime = 0.0;
    private final SlewRateLimiter turnAccel = new SlewRateLimiter(MAX_TURN_ACCEL);
    private double feedingClear = 0.0; // s fed with no ball at the beams
    private boolean ballEntering = false; // the launcher's beam break, last loop
    private double holdUntil = 0.0; // no feeding before this (s): the wheels recovering
    private final boolean moving;

    /**
     * @param moving whether to lead the target by the robot's velocity: a standing robot doesn't,
     *     so the measured wheel speeds' jitter as it turns doesn't move its target
     */
    Aimer(Drive drive, Shooter shooter, boolean moving) {
      this.drive = drive;
      this.shooter = shooter;
      this.moving = moving;
      heading.enableContinuousInput(-Math.PI, Math.PI);
    }

    void initialize() {
      heading.reset();
      turnAccel.reset(drive.getChassisSpeeds().omegaRadiansPerSecond);
      lastTarget = null;
      feedingClear = 0.0;
      ballEntering = false;
      holdUntil = 0.0;
    }

    /** The turn rate (rad/s) that faces the target; feeds when ready. */
    double execute() {
      var pose = drive.getPose();
      var velocity =
          moving
              ? ChassisSpeeds.fromRobotRelativeSpeeds(drive.getChassisSpeeds(), pose.getRotation())
              : new ChassisSpeeds();
      Shot shot = Shooter.aim(pose, velocity, Shooter.hub());
      shooter.prepare(shot);

      // feedforward: how fast the target's direction turns
      double now = Timer.getTimestamp();
      double rate = 0.0;
      if (lastTarget != null && now > lastTime) {
        rate = shot.heading().minus(lastTarget).getRadians() / (now - lastTime);
      }
      lastTarget = shot.heading();
      lastTime = now;
      double omega =
          turnAccel.calculate(
              MathUtil.clamp(
                  rate
                      + heading.calculate(
                          pose.getRotation().getRadians(), shot.heading().getRadians()),
                  -MAX_TURN_RATE,
                  MAX_TURN_RATE));

      double error = Math.abs(pose.getRotation().minus(shot.heading()).getDegrees());
      boolean entering = shooter.ballEnteringLauncher();
      if (entering && !ballEntering) {
        holdUntil = now + RECOVERY;
      }
      ballEntering = entering;
      boolean fire =
          shot.inRange()
              && shooter.ready()
              && error <= Shooter.HEADING_TOLERANCE
              && now >= holdUntil
              && !entering;
      shooter.feed(fire);
      if (fire) {
        feedingClear = shooter.ballAtBeams() ? 0.0 : feedingClear + 0.02;
      }
      Logger.recordOutput("Shoot/Target", shot.target());
      Logger.recordOutput("Shoot/Distance", shot.distance());
      Logger.recordOutput("Shoot/HeadingErrorDegrees", error);
      Logger.recordOutput("Shoot/InRange", shot.inRange());
      Logger.recordOutput("Shoot/Firing", fire);
      return omega;
    }

    boolean empty() {
      return feedingClear >= EMPTY_AFTER;
    }

    void end() {
      shooter.feed(false);
    }
  }

  /**
   * Stands, faces the hub and fires until empty (no ball seen at the beam breaks for {@link
   * #EMPTY_AFTER} s of feeding), or for {@code timeoutSeconds} at most. Prints "shoot: empty" or
   * "shoot: timed out".
   */
  public static Command shootStanding(Drive drive, Shooter shooter, double timeoutSeconds) {
    var aimer = new Aimer(drive, shooter, false);
    return Commands.sequence(
        Commands.runOnce(aimer::initialize),
        Commands.run(
                () ->
                    drive.runVelocity(
                        ChassisSpeeds.fromFieldRelativeSpeeds(
                            0.0, 0.0, aimer.execute(), drive.getRotation())),
                drive,
                shooter)
            .until(aimer::empty)
            .withTimeout(timeoutSeconds)
            .finallyDo(
                () -> {
                  aimer.end();
                  drive.stop();
                }),
        Commands.runOnce(
            () -> System.out.println("shoot: " + (aimer.empty() ? "empty" : "timed out"))));
  }

  /**
   * Aims and fires while a PathPlanner path drives: it takes over the path follower's rotation
   * feedback with the aiming turn rate (the paths keep a constant rotation, so the follower adds no
   * turn of its own). It never ends: run it as a path's deadline partner.
   */
  public static Command shootWhileDriving(Drive drive, Shooter shooter) {
    var aimer = new Aimer(drive, shooter, true);
    var omega = new double[] {0.0};
    return Commands.run(() -> omega[0] = aimer.execute(), shooter)
        .beforeStarting(
            () -> {
              aimer.initialize();
              omega[0] = 0.0;
              PPHolonomicDriveController.overrideRotationFeedback(() -> omega[0]);
            })
        .finallyDo(
            () -> {
              PPHolonomicDriveController.clearRotationFeedbackOverride();
              aimer.end();
            });
  }

  /** Runs the intake inward; the shooter's other parts keep what they were doing. */
  public static Command intake(Shooter shooter) {
    return Commands.startEnd(() -> shooter.intake(true), () -> shooter.intake(false));
  }
}
