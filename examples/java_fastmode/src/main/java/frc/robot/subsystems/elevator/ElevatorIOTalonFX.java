package frc.robot.subsystems.elevator;

import static frc.robot.ScrimmageConstants.*;
import static frc.robot.util.PhoenixUtil.*;

import com.ctre.phoenix6.BaseStatusSignal;
import com.ctre.phoenix6.StatusSignal;
import com.ctre.phoenix6.controls.Follower;
import com.ctre.phoenix6.controls.MotionMagicVoltage;
import com.ctre.phoenix6.controls.VoltageOut;
import com.ctre.phoenix6.hardware.ParentDevice;
import com.ctre.phoenix6.hardware.TalonFX;
import com.ctre.phoenix6.signals.MotorAlignmentValue;
import edu.wpi.first.units.measure.Angle;
import edu.wpi.first.units.measure.AngularVelocity;
import edu.wpi.first.units.measure.Current;
import edu.wpi.first.units.measure.Voltage;

/** Elevator IO implementation for two Talon FXs (Kraken X60s), the second an opposed follower. */
public class ElevatorIOTalonFX implements ElevatorIO {
  private final TalonFX lead = new TalonFX(ELEVATOR_LEAD_ID);
  private final TalonFX follower = new TalonFX(ELEVATOR_FOLLOWER_ID);

  private final MotionMagicVoltage motionMagic = new MotionMagicVoltage(0.0);
  private final VoltageOut voltageOut = new VoltageOut(0.0);

  private final StatusSignal<Angle> position = lead.getPosition();
  private final StatusSignal<AngularVelocity> velocity = lead.getVelocity();
  private final StatusSignal<Voltage> appliedVolts = lead.getMotorVoltage();
  private final StatusSignal<Current> leadStator = lead.getStatorCurrent();
  private final StatusSignal<Current> followerStator = follower.getStatorCurrent();
  private final StatusSignal<Current> leadSupply = lead.getSupplyCurrent();
  private final StatusSignal<Current> followerSupply = follower.getSupplyCurrent();

  public ElevatorIOTalonFX() {
    var config = elevatorConfig(); // shared with ElevatorIOScrimmage
    tryUntilOk(5, () -> lead.getConfigurator().apply(config, 0.25));
    tryUntilOk(5, () -> follower.getConfigurator().apply(config, 0.25));
    tryUntilOk(5, () -> lead.setPosition(0.0, 0.25));
    follower.setControl(new Follower(ELEVATOR_LEAD_ID, MotorAlignmentValue.Opposed));

    BaseStatusSignal.setUpdateFrequencyForAll(
        50.0,
        position,
        velocity,
        appliedVolts,
        leadStator,
        followerStator,
        leadSupply,
        followerSupply);
    ParentDevice.optimizeBusUtilizationForAll(lead, follower);
  }

  @Override
  public void updateInputs(ElevatorIOInputs inputs) {
    inputs.connected =
        BaseStatusSignal.refreshAll(
                position,
                velocity,
                appliedVolts,
                leadStator,
                followerStator,
                leadSupply,
                followerSupply)
            .isOK();
    inputs.positionMeters = position.getValueAsDouble() * ELEVATOR_METERS_PER_ROTATION;
    inputs.velocityMetersPerSec = velocity.getValueAsDouble() * ELEVATOR_METERS_PER_ROTATION;
    inputs.appliedVolts = appliedVolts.getValueAsDouble();
    inputs.statorCurrentAmps =
        new double[] {leadStator.getValueAsDouble(), followerStator.getValueAsDouble()};
    inputs.supplyCurrentAmps =
        new double[] {leadSupply.getValueAsDouble(), followerSupply.getValueAsDouble()};
  }

  @Override
  public void setHeight(double meters) {
    lead.setControl(motionMagic.withPosition(meters / ELEVATOR_METERS_PER_ROTATION));
  }

  @Override
  public void setVoltage(double volts) {
    lead.setControl(voltageOut.withOutput(volts));
  }
}
