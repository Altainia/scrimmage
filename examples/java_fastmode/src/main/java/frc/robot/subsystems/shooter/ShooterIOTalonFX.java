package frc.robot.subsystems.shooter;

import static frc.robot.ScrimmageConstants.*;
import static frc.robot.util.PhoenixUtil.*;

import com.ctre.phoenix6.BaseStatusSignal;
import com.ctre.phoenix6.StatusSignal;
import com.ctre.phoenix6.controls.CoastOut;
import com.ctre.phoenix6.controls.Follower;
import com.ctre.phoenix6.controls.MotionMagicVoltage;
import com.ctre.phoenix6.controls.NeutralOut;
import com.ctre.phoenix6.controls.VelocityVoltage;
import com.ctre.phoenix6.controls.VoltageOut;
import com.ctre.phoenix6.hardware.ParentDevice;
import com.ctre.phoenix6.hardware.TalonFX;
import com.ctre.phoenix6.signals.MotorAlignmentValue;
import edu.wpi.first.units.measure.Angle;
import edu.wpi.first.units.measure.AngularVelocity;
import edu.wpi.first.units.measure.Current;
import edu.wpi.first.units.measure.Voltage;
import edu.wpi.first.wpilibj.DigitalInput;

/**
 * Shooter IO implementation for Talon FXs (Kraken X60s): two on the wheel shaft (the second an
 * opposed follower), one on the hood, one on each roller. The beam breaks are DIOs.
 */
public class ShooterIOTalonFX implements ShooterIO {
  private final TalonFX lead = new TalonFX(SHOOTER_LEAD_ID);
  private final TalonFX follower = new TalonFX(SHOOTER_FOLLOWER_ID);
  private final TalonFX hood = new TalonFX(HOOD_ID);
  private final TalonFX floor = new TalonFX(FLOOR_ID);
  private final TalonFX feeder = new TalonFX(FEEDER_ID);
  private final TalonFX intake = new TalonFX(INTAKE_ID);
  // Beam breaks pull low when broken
  private final DigitalInput feederBeam = new DigitalInput(FEEDER_BEAM_DIO);
  private final DigitalInput launcherBeam = new DigitalInput(LAUNCHER_BEAM_DIO);

  private final VelocityVoltage velocityOut = new VelocityVoltage(0.0);
  private final MotionMagicVoltage motionMagic = new MotionMagicVoltage(0.0);
  private final VoltageOut floorOut = new VoltageOut(0.0);
  private final VoltageOut feederOut = new VoltageOut(0.0);
  private final VoltageOut intakeOut = new VoltageOut(0.0);
  private final NeutralOut neutralOut = new NeutralOut();

  private final StatusSignal<AngularVelocity> wheelVelocity = lead.getVelocity();
  private final StatusSignal<Voltage> wheelVolts = lead.getMotorVoltage();
  private final StatusSignal<Current> leadStator = lead.getStatorCurrent();
  private final StatusSignal<Current> followerStator = follower.getStatorCurrent();
  private final StatusSignal<Current> leadSupply = lead.getSupplyCurrent();
  private final StatusSignal<Current> followerSupply = follower.getSupplyCurrent();
  private final StatusSignal<Angle> hoodPosition = hood.getPosition();
  private final StatusSignal<Voltage> hoodVolts = hood.getMotorVoltage();
  private final StatusSignal<AngularVelocity> floorVelocity = floor.getVelocity();
  private final StatusSignal<AngularVelocity> feederVelocity = feeder.getVelocity();
  private final StatusSignal<AngularVelocity> intakeVelocity = intake.getVelocity();

  public ShooterIOTalonFX() {
    var wheels = shooterConfig(); // shared with ShooterIOScrimmage
    tryUntilOk(5, () -> lead.getConfigurator().apply(wheels, 0.25));
    tryUntilOk(5, () -> follower.getConfigurator().apply(wheels, 0.25));
    follower.setControl(new Follower(SHOOTER_LEAD_ID, MotorAlignmentValue.Opposed));
    var hoodConfig = hoodConfig();
    tryUntilOk(5, () -> hood.getConfigurator().apply(hoodConfig, 0.25));
    tryUntilOk(5, () -> hood.setPosition(0.0, 0.25)); // on its low stop
    var rollers = rollerConfig();
    for (var roller : new TalonFX[] {floor, feeder, intake}) {
      tryUntilOk(5, () -> roller.getConfigurator().apply(rollers, 0.25));
    }

    // The wheel speed gates feeding: read it as often as the robot loop runs
    BaseStatusSignal.setUpdateFrequencyForAll(100.0, wheelVelocity, hoodPosition);
    BaseStatusSignal.setUpdateFrequencyForAll(
        50.0,
        wheelVolts,
        leadStator,
        followerStator,
        leadSupply,
        followerSupply,
        hoodVolts,
        floorVelocity,
        feederVelocity,
        intakeVelocity);
    ParentDevice.optimizeBusUtilizationForAll(lead, follower, hood, floor, feeder, intake);
  }

  @Override
  public void updateInputs(ShooterIOInputs inputs) {
    inputs.connected =
        BaseStatusSignal.refreshAll(
                wheelVelocity,
                wheelVolts,
                leadStator,
                followerStator,
                leadSupply,
                followerSupply,
                hoodPosition,
                hoodVolts,
                floorVelocity,
                feederVelocity,
                intakeVelocity)
            .isOK();
    inputs.wheelRps = wheelVelocity.getValueAsDouble();
    inputs.wheelAppliedVolts = wheelVolts.getValueAsDouble();
    inputs.wheelStatorCurrentAmps =
        new double[] {leadStator.getValueAsDouble(), followerStator.getValueAsDouble()};
    inputs.wheelSupplyCurrentAmps =
        new double[] {leadSupply.getValueAsDouble(), followerSupply.getValueAsDouble()};
    inputs.hoodDegrees = HOOD_MIN_DEGREES + hoodPosition.getValueAsDouble() * 360.0;
    inputs.hoodAppliedVolts = hoodVolts.getValueAsDouble();
    inputs.floorRps = floorVelocity.getValueAsDouble();
    inputs.feederRps = feederVelocity.getValueAsDouble();
    inputs.intakeRps = intakeVelocity.getValueAsDouble();
    inputs.feederBeamBroken = !feederBeam.get();
    inputs.launcherBeamBroken = !launcherBeam.get();
  }

  @Override
  public void setWheelRps(double rps) {
    lead.setControl(velocityOut.withVelocity(rps));
  }

  @Override
  public void stopWheel() {
    lead.setControl(new CoastOut());
  }

  @Override
  public void setHoodDegrees(double degrees) {
    hood.setControl(motionMagic.withPosition((degrees - HOOD_MIN_DEGREES) / 360.0));
  }

  /**
   * A roller at {@code volts}; at 0 V, neutral (brake): the rollers stop at once, where 0 V out
   * would slow them only as fast as the stator current limit lets them brake.
   */
  private void run(TalonFX roller, VoltageOut request, double volts) {
    if (volts == 0.0) {
      roller.setControl(neutralOut);
    } else {
      roller.setControl(request.withOutput(volts));
    }
  }

  @Override
  public void setFloorVolts(double volts) {
    run(floor, floorOut, volts);
  }

  @Override
  public void setFeederVolts(double volts) {
    run(feeder, feederOut, volts);
  }

  @Override
  public void setIntakeVolts(double volts) {
    run(intake, intakeOut, volts);
  }
}
