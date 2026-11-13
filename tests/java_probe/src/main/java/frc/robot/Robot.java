package frc.robot;

// libgrapplefrc (vendordeps/libgrapplefrc2026.json) is Grapple Robotics' software under the Grapple Modified MIT
// License; its copyright and permission notice is vendordeps/libgrapplefrc-LICENSE.txt.
import au.grapplerobotics.ConfigurationFailedException;
import au.grapplerobotics.LaserCan;
import au.grapplerobotics.interfaces.LaserCanInterface;
import com.ctre.phoenix6.StatusCode;
import com.ctre.phoenix6.configs.CANcoderConfiguration;
import com.ctre.phoenix6.configs.TalonFXConfiguration;
import com.ctre.phoenix6.controls.DutyCycleOut;
import com.ctre.phoenix6.controls.MotionMagicVoltage;
import com.ctre.phoenix6.hardware.CANcoder;
import com.ctre.phoenix6.hardware.CANrange;
import com.ctre.phoenix6.hardware.Pigeon2;
import com.ctre.phoenix6.hardware.TalonFX;
import com.ctre.phoenix6.signals.InvertedValue;
import com.ctre.phoenix6.signals.NeutralModeValue;
import edu.wpi.first.wpilibj.DriverStation;
import edu.wpi.first.wpilibj.TimedRobot;
import java.util.function.Supplier;

/** Test robot for scrimmage's Java tests. PROBE_MODE selects behavior:
 *  "read"  - print positions each loop (default)
 *  "write" - also write sim state every loop, like a robot-side sim model
 *  "mm"    - TalonFX 1 runs Motion Magic to 20 rotations when enabled; prints every 100 ms of robot time
 *  "match" - in teleop, prints the game data and alliance once, and again when the game data changes
 *  "range" - prints the CANranges' and LaserCANs' readings every 100 ms of robot time; LaserCAN 9 starts in long
 *            ranging and switches to short when the robot is enabled */
public class Robot extends TimedRobot {
  private final TalonFX motor = new TalonFX(1);
  private final TalonFX inverted = new TalonFX(4);
  private final CANcoder cancoder = new CANcoder(2);
  private final Pigeon2 pigeon = new Pigeon2(3);
  private final CANrange range5 = new CANrange(5);
  private final CANrange range8 = new CANrange(8);
  private final LaserCan laser7 = new LaserCan(7);
  private static final LaserCan LASER9 = new LaserCan(9);   // made in a static initializer, before the robot
  private final String mode = System.getenv().getOrDefault("PROBE_MODE", "read");
  private int loops = 0;
  private String gameData = null;   // "match": the game data last printed

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
    if (mode.equals("range")) {
      rangingMode(LASER9, LaserCanInterface.RangingMode.LONG);
    }
  }

  /** Grapple's idiom: set the ranging mode, catching the failure. Prints "cfg lasercan <mode> ok" or the failure. */
  private static void rangingMode(LaserCan laser, LaserCanInterface.RangingMode m) {
    try {
      laser.setRangingMode(m);
      System.out.printf("cfg lasercan %s ok%n", m);
    } catch (ConfigurationFailedException e) {
      System.out.printf("cfg lasercan %s failed: %s%n", m, e.getMessage());
    }
  }

  private static String reading(LaserCan laser) {
    LaserCan.Measurement m = laser.getMeasurement();
    return m == null ? "null" : m.status + "," + m.distance_mm + "," + (m.is_long ? "long" : "short");
  }

  private void rangePeriodic() {
    if (++loops % 5 == 0) {   // every 100 ms of robot time
      System.out.printf("range cr5=%.4f,%s cr8=%.4f,%s lc7=%s lc9=%s loop=%d%n",
          range5.getDistance().getValueAsDouble(), range5.getIsDetected().getValue(),
          range8.getDistance().getValueAsDouble(), range8.getIsDetected().getValue(),
          reading(laser7), reading(LASER9), loops);
    }
  }

  @Override
  public void teleopInit() {
    if (mode.equals("range")) {
      rangingMode(LASER9, LaserCanInterface.RangingMode.SHORT);
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

  private void matchPeriodic() {
    if (!DriverStation.isTeleopEnabled()) {
      return;
    }
    String data = DriverStation.getGameSpecificMessage();
    if (!data.equals(gameData)) {
      gameData = data;
      System.out.printf("match data='%s' alliance=%s%n", data,
          DriverStation.getAlliance().map(Enum::name).orElse("none"));
    }
  }

  @Override
  public void robotPeriodic() {
    if (mode.equals("match")) {
      matchPeriodic();
      return;
    }
    if (mode.equals("mm")) {
      motionMagicPeriodic();
      return;
    }
    if (mode.equals("range")) {
      rangePeriodic();
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
