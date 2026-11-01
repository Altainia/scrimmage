package frc.robot.subsystems.shooter;

import static frc.robot.ScrimmageConstants.*;

import edu.wpi.first.wpilibj.DigitalInput;
import scrimmage.sim.ControlRequest;
import scrimmage.sim.ScrimmageKraken;

/**
 * Shooter IO implementation for scrimmage's fast mode: simulated Krakens configured from the same
 * Phoenix configs as {@link ShooterIOTalonFX}. Bound as {@code "SimMotor:shooter_lead"}, {@code
 * "SimMotor:shooter_follower"}, {@code "SimMotor:hood"}, {@code "SimMotor:floor"}, {@code
 * "SimMotor:feeder"} and {@code "SimMotor:intake"}; the beam breaks are ordinary DIOs.
 */
public class ShooterIOScrimmage implements ShooterIO {
  private final ScrimmageKraken lead;
  private final ScrimmageKraken follower;
  private final ScrimmageKraken hood;
  private final ScrimmageKraken floor;
  private final ScrimmageKraken feeder;
  private final ScrimmageKraken intake;
  // Beam breaks pull low when broken
  private final DigitalInput feederBeam = new DigitalInput(FEEDER_BEAM_DIO);
  private final DigitalInput launcherBeam = new DigitalInput(LAUNCHER_BEAM_DIO);

  public ShooterIOScrimmage() {
    var wheels = krakenSettings(shooterConfig());
    lead = new ScrimmageKraken("shooter_lead", wheels);
    follower = new ScrimmageKraken("shooter_follower", wheels);
    follower.follow(lead, true);
    hood = new ScrimmageKraken("hood", krakenSettings(hoodConfig()));
    hood.setPosition(0.0); // on its low stop
    var rollers = krakenSettings(rollerConfig());
    floor = new ScrimmageKraken("floor", rollers);
    feeder = new ScrimmageKraken("feeder", rollers);
    intake = new ScrimmageKraken("intake", rollers);
  }

  @Override
  public void updateInputs(ShooterIOInputs inputs) {
    inputs.connected = true;
    inputs.wheelRps = lead.getVelocity();
    inputs.wheelAppliedVolts = lead.getMotorVoltage();
    inputs.wheelStatorCurrentAmps =
        new double[] {lead.getStatorCurrent(), follower.getStatorCurrent()};
    inputs.wheelSupplyCurrentAmps =
        new double[] {lead.getSupplyCurrent(), follower.getSupplyCurrent()};
    inputs.hoodDegrees = HOOD_MIN_DEGREES + hood.getPosition() * 360.0;
    inputs.hoodAppliedVolts = hood.getMotorVoltage();
    inputs.floorRps = floor.getVelocity();
    inputs.feederRps = feeder.getVelocity();
    inputs.intakeRps = intake.getVelocity();
    inputs.feederBeamBroken = !feederBeam.get();
    inputs.launcherBeamBroken = !launcherBeam.get();
  }

  @Override
  public void setWheelRps(double rps) {
    lead.setControl(ControlRequest.velocity(rps));
  }

  @Override
  public void stopWheel() {
    lead.setControl(ControlRequest.neutral());
  }

  @Override
  public void setHoodDegrees(double degrees) {
    hood.setControl(ControlRequest.motionMagic((degrees - HOOD_MIN_DEGREES) / 360.0));
  }

  /** A roller at {@code volts}; at 0 V, neutral (brake), as {@link ShooterIOTalonFX} does. */
  private static void run(ScrimmageKraken roller, double volts) {
    roller.setControl(volts == 0.0 ? ControlRequest.neutral() : ControlRequest.voltage(volts));
  }

  @Override
  public void setFloorVolts(double volts) {
    run(floor, volts);
  }

  @Override
  public void setFeederVolts(double volts) {
    run(feeder, volts);
  }

  @Override
  public void setIntakeVolts(double volts) {
    run(intake, volts);
  }
}
