package frc.robot;

import com.ctre.phoenix6.StatusCode;
import com.ctre.phoenix6.configs.CANcoderConfiguration;
import com.ctre.phoenix6.configs.TalonFXConfiguration;
import com.ctre.phoenix6.controls.DutyCycleOut;
import com.ctre.phoenix6.controls.MotionMagicVoltage;
import com.ctre.phoenix6.hardware.CANcoder;
import com.ctre.phoenix6.hardware.Pigeon2;
import com.ctre.phoenix6.hardware.TalonFX;
import com.ctre.phoenix6.signals.InvertedValue;
import com.ctre.phoenix6.signals.NeutralModeValue;
import edu.wpi.first.wpilibj.TimedRobot;
import java.util.function.Supplier;

/** Test robot for scrimmage's Java tests. PROBE_MODE selects behavior:
 *  "read"  - print positions each loop (default)
 *  "write" - also write sim state every loop, like a robot-side sim model
 *  "mm"    - TalonFX 1 runs Motion Magic to 20 rotations when enabled; prints every 100 ms of robot time */
public class Robot extends TimedRobot {
  private final TalonFX motor = new TalonFX(1);
  private final TalonFX inverted = new TalonFX(4);
  private final CANcoder cancoder = new CANcoder(2);
  private final Pigeon2 pigeon = new Pigeon2(3);
  private final String mode = System.getenv().getOrDefault("PROBE_MODE", "read");
  private int loops = 0;

  public Robot() {
    var brake = new TalonFXConfiguration();
    brake.MotorOutput.NeutralMode = NeutralModeValue.Brake;   // matches neutral = "brake" in scrimmage.toml
    apply("brake", () -> motor.getConfigurator().apply(brake));
    var inv = new TalonFXConfiguration();
    inv.MotorOutput.Inverted = InvertedValue.Clockwise_Positive;
    inv.MotorOutput.NeutralMode = NeutralModeValue.Brake;
    apply("inv", () -> inverted.getConfigurator().apply(inv));
    var cc = new CANcoderConfiguration();
    cc.MagnetSensor.MagnetOffset = 0.25;
    apply("cancoder", () -> cancoder.getConfigurator().apply(cc));
    if (mode.equals("mm")) {
      var mm = new TalonFXConfiguration();
      mm.MotorOutput.NeutralMode = NeutralModeValue.Brake;
      mm.MotionMagic.MotionMagicCruiseVelocity = 20;   // rps
      mm.MotionMagic.MotionMagicAcceleration = 40;     // rps/s
      mm.Slot0.kP = 2;
      mm.Slot0.kI = 0.5;
      apply("mm", () -> motor.getConfigurator().apply(mm));
    }
  }

  /** CTRE's idiom: retry a config apply up to 5 times until it succeeds. Prints "cfg <name> <status>" once. */
  private static void apply(String name, Supplier<StatusCode> apply) {
    StatusCode status = StatusCode.StatusCodeNotInitialized;
    for (int i = 0; i < 5 && !status.isOK(); i++) {
      status = apply.get();
    }
    System.out.printf("cfg %s %s%n", name, status);
  }

  // FOC off: the result shouldn't depend on whether simulated Pro/FOC is available
  private final MotionMagicVoltage mmRequest = new MotionMagicVoltage(20).withEnableFOC(false);

  private void motionMagicPeriodic() {
    if (isEnabled()) {
      motor.setControl(mmRequest);
    }
    if (++loops % 5 == 0) {   // every 100 ms of robot time
      System.out.printf("mm pos=%.4f vel=%.4f ref=%.4f out=%.3f enable=%s loop=%d%n",
          motor.getPosition().getValueAsDouble(), motor.getVelocity().getValueAsDouble(),
          motor.getClosedLoopReference().getValueAsDouble(), motor.getMotorVoltage().getValueAsDouble(),
          motor.getDeviceEnable().getValue(), loops);
    }
  }

  @Override
  public void robotPeriodic() {
    if (mode.equals("mm")) {
      motionMagicPeriodic();
      return;
    }
    if (mode.equals("write")) {
      motor.getSimState().setRawRotorPosition(5.0);
      cancoder.getSimState().setRawPosition(0.4);
      pigeon.getSimState().setRawYaw(33.0);
      motor.getSimState().setForwardLimit(true);   // scrimmage has no limit-switch binding: never blocked
    }
    motor.setControl(new DutyCycleOut(0.3));
    inverted.setControl(new DutyCycleOut(0.3));   // Clockwise_Positive: drives the rotor clockwise (negative raw)
    if (++loops % 25 == 0) {
      System.out.printf("probe pos=%.3f inv=%.3f cc=%.4f yaw=%.2f loop=%d%n",
          motor.getPosition().getValueAsDouble(), inverted.getPosition().getValueAsDouble(),
          cancoder.getPosition().getValueAsDouble(), pigeon.getYaw().getValueAsDouble(), loops);
    }
  }
}
