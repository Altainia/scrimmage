package frc.robot.subsystems.arm;

import org.littletonrobotics.junction.AutoLog;

/** The arm's hardware: one Kraken through a gearbox, angles in degrees (0 = horizontal forward). */
public interface ArmIO {
  @AutoLog
  public static class ArmIOInputs {
    public boolean connected = false;
    public double positionDegrees = 0.0;
    public double velocityDegreesPerSec = 0.0;
    public double appliedVolts = 0.0;
    public double statorCurrentAmps = 0.0;
    public double supplyCurrentAmps = 0.0;
  }

  /** Updates the set of loggable inputs. */
  public default void updateInputs(ArmIOInputs inputs) {}

  /** Moves to an angle with a Motion Magic profile, kG scaled by the cosine of the angle. */
  public default void setAngle(double degrees) {}

  /** Runs open loop at the given voltage. */
  public default void setVoltage(double volts) {}
}
