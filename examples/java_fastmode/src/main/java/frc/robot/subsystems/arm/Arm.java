package frc.robot.subsystems.arm;

import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.Commands;
import edu.wpi.first.wpilibj2.command.SubsystemBase;
import org.littletonrobotics.junction.AutoLogOutput;
import org.littletonrobotics.junction.Logger;

/** A single-jointed arm moved with Motion Magic, its kG scaled by the cosine of its angle. */
public class Arm extends SubsystemBase {
  /** How close to the target angle counts as there (degrees). */
  public static final double TOLERANCE = 1.0;
  /** A move that takes longer than this ends anyway, so a stuck arm can't hang an auto (s). */
  public static final double MOVE_TIMEOUT = 3.0;

  private final ArmIO io;
  private final ArmIOInputsAutoLogged inputs = new ArmIOInputsAutoLogged();
  private double targetDegrees = 0.0;

  public Arm(ArmIO io) {
    this.io = io;
  }

  @Override
  public void periodic() {
    io.updateInputs(inputs);
    Logger.processInputs("Arm", inputs);
  }

  @AutoLogOutput(key = "Arm/TargetDegrees")
  public double getTargetDegrees() {
    return targetDegrees;
  }

  public double getAngleDegrees() {
    return inputs.positionDegrees;
  }

  @AutoLogOutput(key = "Arm/AtTarget")
  public boolean atTarget() {
    return Math.abs(inputs.positionDegrees - targetDegrees) < TOLERANCE;
  }

  /**
   * Moves to {@code degrees} and ends there, printing "arm: at <angle> deg"; or, after {@link
   * #MOVE_TIMEOUT}, ends wherever it is, printing "arm: timed out at <angle> deg".
   */
  public Command moveTo(double degrees) {
    return Commands.sequence(
        runOnce(
            () -> {
              targetDegrees = degrees;
              io.setAngle(degrees);
            }),
        Commands.waitUntil(this::atTarget).withTimeout(MOVE_TIMEOUT),
        Commands.runOnce(
            () ->
                System.out.printf(
                    "arm: %s %.1f deg%n",
                    atTarget() ? "at" : "timed out at", inputs.positionDegrees)));
  }
}
