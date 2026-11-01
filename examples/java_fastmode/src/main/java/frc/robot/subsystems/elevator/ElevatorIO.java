package frc.robot.subsystems.elevator;

import org.littletonrobotics.junction.AutoLog;

/** The elevator's hardware: a lead Kraken and an opposed follower, heights in meters. */
public interface ElevatorIO {
  @AutoLog
  public static class ElevatorIOInputs {
    public boolean connected = false;
    public double positionMeters = 0.0;
    public double velocityMetersPerSec = 0.0;
    public double appliedVolts = 0.0;
    public double[] statorCurrentAmps = new double[] {}; // lead, follower
    public double[] supplyCurrentAmps = new double[] {};
  }

  /** Updates the set of loggable inputs. */
  public default void updateInputs(ElevatorIOInputs inputs) {}

  /** Moves to a height with a Motion Magic profile. */
  public default void setHeight(double meters) {}

  /** Runs open loop at the given voltage. */
  public default void setVoltage(double volts) {}
}
