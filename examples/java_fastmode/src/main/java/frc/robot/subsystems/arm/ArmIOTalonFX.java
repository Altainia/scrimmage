package frc.robot.subsystems.arm;

import static frc.robot.ScrimmageConstants.*;
import static frc.robot.util.PhoenixUtil.*;

import com.ctre.phoenix6.BaseStatusSignal;
import com.ctre.phoenix6.StatusSignal;
import com.ctre.phoenix6.controls.MotionMagicVoltage;
import com.ctre.phoenix6.controls.VoltageOut;
import com.ctre.phoenix6.hardware.ParentDevice;
import com.ctre.phoenix6.hardware.TalonFX;
import edu.wpi.first.units.measure.Angle;
import edu.wpi.first.units.measure.AngularVelocity;
import edu.wpi.first.units.measure.Current;
import edu.wpi.first.units.measure.Voltage;

/** Arm IO implementation for a Talon FX (Kraken X60) through a gearbox. */
public class ArmIOTalonFX implements ArmIO {
  private final TalonFX motor = new TalonFX(ARM_ID);

  private final MotionMagicVoltage motionMagic = new MotionMagicVoltage(0.0);
  private final VoltageOut voltageOut = new VoltageOut(0.0);

  private final StatusSignal<Angle> position = motor.getPosition();
  private final StatusSignal<AngularVelocity> velocity = motor.getVelocity();
  private final StatusSignal<Voltage> appliedVolts = motor.getMotorVoltage();
  private final StatusSignal<Current> stator = motor.getStatorCurrent();
  private final StatusSignal<Current> supply = motor.getSupplyCurrent();

  public ArmIOTalonFX() {
    var config = armConfig(); // shared with ArmIOScrimmage
    tryUntilOk(5, () -> motor.getConfigurator().apply(config, 0.25));
    tryUntilOk(5, () -> motor.setPosition(ARM_START_DEGREES / 360.0, 0.25));

    BaseStatusSignal.setUpdateFrequencyForAll(
        50.0, position, velocity, appliedVolts, stator, supply);
    ParentDevice.optimizeBusUtilizationForAll(motor);
  }

  @Override
  public void updateInputs(ArmIOInputs inputs) {
    inputs.connected =
        BaseStatusSignal.refreshAll(position, velocity, appliedVolts, stator, supply).isOK();
    inputs.positionDegrees = position.getValueAsDouble() * 360.0;
    inputs.velocityDegreesPerSec = velocity.getValueAsDouble() * 360.0;
    inputs.appliedVolts = appliedVolts.getValueAsDouble();
    inputs.statorCurrentAmps = stator.getValueAsDouble();
    inputs.supplyCurrentAmps = supply.getValueAsDouble();
  }

  @Override
  public void setAngle(double degrees) {
    motor.setControl(motionMagic.withPosition(degrees / 360.0));
  }

  @Override
  public void setVoltage(double volts) {
    motor.setControl(voltageOut.withOutput(volts));
  }
}
