package frc.robot.subsystems.elevator;

import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.Commands;
import edu.wpi.first.wpilibj2.command.SubsystemBase;
import org.littletonrobotics.junction.AutoLogOutput;
import org.littletonrobotics.junction.Logger;

/** A two-Kraken elevator moved with Motion Magic. */
public class Elevator extends SubsystemBase {
  /** How close to the target height counts as there (m). */
  public static final double TOLERANCE = 0.01;
  /** A move that takes longer than this ends anyway, so a stuck elevator can't hang an auto (s). */
  public static final double MOVE_TIMEOUT = 3.0;

  private final ElevatorIO io;
  private final ElevatorIOInputsAutoLogged inputs = new ElevatorIOInputsAutoLogged();
  private double targetMeters = 0.0;

  public Elevator(ElevatorIO io) {
    this.io = io;
  }

  @Override
  public void periodic() {
    io.updateInputs(inputs);
    Logger.processInputs("Elevator", inputs);
  }

  @AutoLogOutput(key = "Elevator/TargetMeters")
  public double getTargetMeters() {
    return targetMeters;
  }

  public double getHeightMeters() {
    return inputs.positionMeters;
  }

  @AutoLogOutput(key = "Elevator/AtTarget")
  public boolean atTarget() {
    return Math.abs(inputs.positionMeters - targetMeters) < TOLERANCE;
  }

  /**
   * Moves to {@code meters} and ends there, printing "elevator: at <height> m"; or, after {@link
   * #MOVE_TIMEOUT}, ends wherever it is, printing "elevator: timed out at <height> m".
   */
  public Command moveTo(double meters) {
    return Commands.sequence(
        runOnce(
            () -> {
              targetMeters = meters;
              io.setHeight(meters);
            }),
        Commands.waitUntil(this::atTarget).withTimeout(MOVE_TIMEOUT),
        Commands.runOnce(
            () ->
                System.out.printf(
                    "elevator: %s %.2f m%n",
                    atTarget() ? "at" : "timed out at", inputs.positionMeters)));
  }
}
