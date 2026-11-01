package frc.robot.subsystems.arm;

import static frc.robot.ScrimmageConstants.*;

import scrimmage.sim.ControlRequest;
import scrimmage.sim.ScrimmageKraken;

/**
 * Arm IO implementation for scrimmage's fast mode: a simulated Kraken configured from the same
 * Phoenix config as {@link ArmIOTalonFX}. Bound as {@code "SimMotor:arm"}.
 */
public class ArmIOScrimmage implements ArmIO {
  private final ScrimmageKraken motor;

  public ArmIOScrimmage() {
    motor = new ScrimmageKraken("arm", krakenSettings(armConfig()));
    motor.setPosition(ARM_START_DEGREES / 360.0);
  }

  @Override
  public void updateInputs(ArmIOInputs inputs) {
    inputs.connected = true;
    inputs.positionDegrees = motor.getPosition() * 360.0;
    inputs.velocityDegreesPerSec = motor.getVelocity() * 360.0;
    inputs.appliedVolts = motor.getMotorVoltage();
    inputs.statorCurrentAmps = motor.getStatorCurrent();
    inputs.supplyCurrentAmps = motor.getSupplyCurrent();
  }

  @Override
  public void setAngle(double degrees) {
    motor.setControl(ControlRequest.motionMagic(degrees / 360.0));
  }

  @Override
  public void setVoltage(double volts) {
    motor.setControl(ControlRequest.voltage(volts));
  }
}
