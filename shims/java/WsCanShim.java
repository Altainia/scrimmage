// UNTESTED TEMPLATE. Copy into your robot project (e.g. src/main/java/frc/robot/sim/) and adjust the package.
//
// Vendor motor controllers (CTRE Phoenix 6, REVLib) simulate themselves inside the robot process and
// never show up on the HALSim WebSocket. This shim republishes each controller as the protocol's
// standard CANMotor / CANEncoder devices, so an external physics engine (scrimmage) can drive them:
//
//   robot code -> controller sim state -> [this shim] -> "CANMotor:TalonFX[3]"   -> scrimmage applies torque
//   scrimmage joint -> "CANEncoder:TalonFX[3]" -> [this shim] -> controller sim state -> your code's getPosition()
//
// Matching scrimmage config:
//   [[motor]]   can = "TalonFX[3]", joint = "arm", motor = "KrakenX60", gear_ratio = 50
//   [[encoder]] can = "TalonFX[3]", joint = "arm", ratio = 50     # rotor rotations per joint rotation
//
// Usage, in Robot.java:
//   private final List<WsCanShim> shims = new ArrayList<>();
//   public void simulationInit() { shims.add(WsCanShim.forTalonFX(armMotor)); }
//   public void simulationPeriodic() { shims.forEach(WsCanShim::update); }

package frc.robot.sim;

import edu.wpi.first.hal.SimBoolean;
import edu.wpi.first.hal.SimDevice;
import edu.wpi.first.hal.SimDouble;
import java.util.function.BooleanSupplier;
import java.util.function.DoubleConsumer;
import java.util.function.DoubleSupplier;

public final class WsCanShim {
  private final SimDevice motorDev;
  private final SimDevice encoderDev;
  private final SimDouble percentOutput;
  private final SimBoolean brakeMode;
  private final SimDouble busVoltage;
  private final SimDouble position;
  private final SimDouble velocity;

  private final DoubleSupplier appliedVolts; // volts the controller is applying to the motor (signed)
  private final BooleanSupplier isBrake;
  private final DoubleConsumer setSupplyVoltage;
  private final DoubleConsumer setRotorRotations;
  private final DoubleConsumer setRotorRps;

  /**
   * Generic constructor: hand it lambdas for whatever controller you use.
   *
   * @param name device name as scrimmage will see it, e.g. "TalonFX[3]"
   */
  public WsCanShim(
      String name,
      DoubleSupplier appliedVolts,
      BooleanSupplier isBrake,
      DoubleConsumer setSupplyVoltage,
      DoubleConsumer setRotorRotations,
      DoubleConsumer setRotorRps) {
    this.appliedVolts = appliedVolts;
    this.isBrake = isBrake;
    this.setSupplyVoltage = setSupplyVoltage;
    this.setRotorRotations = setRotorRotations;
    this.setRotorRps = setRotorRps;

    // The HALSim WebSocket layer turns "CANMotor:<name>" into {"type":"CANMotor","device":"<name>"}.
    motorDev = SimDevice.create("CANMotor:" + name);
    encoderDev = SimDevice.create("CANEncoder:" + name);
    if (motorDev == null || encoderDev == null) {
      throw new IllegalStateException("WsCanShim only works in simulation");
    }
    percentOutput = motorDev.createDouble("percentOutput", SimDevice.Direction.kOutput, 0.0);
    brakeMode = motorDev.createBoolean("brakeMode", SimDevice.Direction.kOutput, true);
    motorDev.createDouble("motorCurrent", SimDevice.Direction.kInput, 0.0);
    motorDev.createDouble("supplyCurrent", SimDevice.Direction.kInput, 0.0);
    busVoltage = motorDev.createDouble("busVoltage", SimDevice.Direction.kInput, 12.0);
    position = encoderDev.createDouble("position", SimDevice.Direction.kInput, 0.0);
    velocity = encoderDev.createDouble("velocity", SimDevice.Direction.kInput, 0.0);
  }

  /** Call every loop from simulationPeriodic(). */
  public void update() {
    double vbus = busVoltage.get();
    if (vbus < 1.0) {
      vbus = 12.0; // no physics connected yet
    }
    setSupplyVoltage.accept(vbus);
    percentOutput.set(Math.max(-1.0, Math.min(1.0, appliedVolts.getAsDouble() / vbus)));
    brakeMode.set(isBrake.getAsBoolean());
    setRotorRotations.accept(position.get());
    setRotorRps.accept(velocity.get());
  }

  // ------------------------------------------------------------------ convenience factories

  /**
   * CTRE Phoenix 6 TalonFX / Kraken. Requires the Phoenix 6 vendordep. The sim state's motor voltage
   * already includes the controller's inversion and closed-loop output, so scrimmage only sees the
   * electrical result, just like a real motor would.
   */
  public static WsCanShim forTalonFX(com.ctre.phoenix6.hardware.TalonFX talon) {
    var sim = talon.getSimState();
    // Read the neutral mode once; change this if your code switches it at runtime.
    var cfg = new com.ctre.phoenix6.configs.MotorOutputConfigs();
    talon.getConfigurator().refresh(cfg);
    boolean brake = cfg.NeutralMode == com.ctre.phoenix6.signals.NeutralModeValue.Brake;
    return new WsCanShim(
        "TalonFX[" + talon.getDeviceID() + "]",
        sim::getMotorVoltage,
        () -> brake,
        sim::setSupplyVoltage,
        sim::setRawRotorPosition,
        sim::setRotorVelocity);
  }
}
