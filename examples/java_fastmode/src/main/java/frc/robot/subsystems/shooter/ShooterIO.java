package frc.robot.subsystems.shooter;

import org.littletonrobotics.junction.AutoLog;

/**
 * The shooter's hardware: two Krakens on the wheel shaft (the second an opposed follower), a hood
 * on its own Kraken, the hopper floor, feeder and intake rollers, and two beam breaks (halfway
 * along the feeder, and at the launcher's entry).
 */
public interface ShooterIO {
  @AutoLog
  public static class ShooterIOInputs {
    public boolean connected = false;
    public double wheelRps = 0.0;
    public double wheelAppliedVolts = 0.0;
    public double[] wheelStatorCurrentAmps = new double[] {};
    public double[] wheelSupplyCurrentAmps = new double[] {};
    /** The hood's exit angle (degrees above horizontal). */
    public double hoodDegrees = 0.0;

    public double hoodAppliedVolts = 0.0;
    public double floorRps = 0.0;
    public double feederRps = 0.0;
    public double intakeRps = 0.0;
    public boolean feederBeamBroken = false;
    public boolean launcherBeamBroken = false;
  }

  /** Updates the set of loggable inputs. */
  public default void updateInputs(ShooterIOInputs inputs) {}

  /** Runs the wheels at a speed (wheel rps), closed loop. */
  public default void setWheelRps(double rps) {}

  /** Lets the wheels coast. */
  public default void stopWheel() {}

  /** Moves the hood to an exit angle (degrees above horizontal) with Motion Magic. */
  public default void setHoodDegrees(double degrees) {}

  public default void setFloorVolts(double volts) {}

  public default void setFeederVolts(double volts) {}

  public default void setIntakeVolts(double volts) {}
}
