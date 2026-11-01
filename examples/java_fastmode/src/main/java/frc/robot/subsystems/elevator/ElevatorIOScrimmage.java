package frc.robot.subsystems.elevator;

import static frc.robot.ScrimmageConstants.*;

import scrimmage.sim.ControlRequest;
import scrimmage.sim.ScrimmageKraken;

/**
 * Elevator IO implementation for scrimmage's fast mode: two simulated Krakens, configured from the
 * same Phoenix config as {@link ElevatorIOTalonFX}, the second an opposed follower. Bound as {@code
 * "SimMotor:elevator_lead"} and {@code "SimMotor:elevator_follower"}.
 */
public class ElevatorIOScrimmage implements ElevatorIO {
  private final ScrimmageKraken lead;
  private final ScrimmageKraken follower;

  public ElevatorIOScrimmage() {
    var settings = krakenSettings(elevatorConfig());
    lead = new ScrimmageKraken("elevator_lead", settings);
    follower = new ScrimmageKraken("elevator_follower", settings);
    lead.setPosition(0.0);
    follower.follow(lead, true);
  }

  @Override
  public void updateInputs(ElevatorIOInputs inputs) {
    inputs.connected = true;
    inputs.positionMeters = lead.getPosition() * ELEVATOR_METERS_PER_ROTATION;
    inputs.velocityMetersPerSec = lead.getVelocity() * ELEVATOR_METERS_PER_ROTATION;
    inputs.appliedVolts = lead.getMotorVoltage();
    inputs.statorCurrentAmps = new double[] {lead.getStatorCurrent(), follower.getStatorCurrent()};
    inputs.supplyCurrentAmps = new double[] {lead.getSupplyCurrent(), follower.getSupplyCurrent()};
  }

  @Override
  public void setHeight(double meters) {
    lead.setControl(ControlRequest.motionMagic(meters / ELEVATOR_METERS_PER_ROTATION));
  }

  @Override
  public void setVoltage(double volts) {
    lead.setControl(ControlRequest.voltage(volts));
  }
}
