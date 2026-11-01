package frc.robot;

import com.revrobotics.PersistMode;
import com.revrobotics.ResetMode;
import com.revrobotics.sim.SparkMaxSim;
import com.revrobotics.spark.FeedbackSensor;
import com.revrobotics.spark.SparkBase.ControlType;
import com.revrobotics.spark.SparkLowLevel.MotorType;
import com.revrobotics.spark.SparkMax;
import com.revrobotics.spark.config.LimitSwitchConfig.Behavior;
import com.revrobotics.spark.config.SparkBaseConfig.IdleMode;
import com.revrobotics.spark.config.SparkMaxConfig;
import edu.wpi.first.math.system.plant.DCMotor;
import edu.wpi.first.wpilibj.Joystick;
import edu.wpi.first.wpilibj.RobotController;
import edu.wpi.first.wpilibj.TimedRobot;
import edu.wpi.first.wpilibj.Timer;

/**
 * Test robot for scrimmage's REV tests (tests/rev_probe.toml): an arm on SPARK MAX 1 (NEO, absolute encoder on its
 * data port, forward limit switch) with SPARK MAX 2 following it inverted, and a flywheel on SPARK MAX 3 (created
 * lazily in teleopInit) and SPARK MAX 4 (follows CAN id 9, which this program never creates).
 *
 * <p>Environment: PROBE_INVERT=1 configures SPARK 1 inverted, and its absolute encoder inverted; PROBE_FEEDBACK=abs
 * closes the arm's loop on the absolute encoder instead of the built-in encoder.
 *
 * <p>Joystick 0, in teleop and disabled alike (a disabled SPARK ignores what it is sent): axis 0 is the arm's duty
 * cycle; holding button 1 runs a position PID, button 2 a MAXMotion move, both to axis 1 x 90 (degrees); pressing
 * button 3 zeroes the arm's and the follower's built-in encoders. Prints one "rev ..." line on each of the first 5
 * loops, then every 5 loops.
 */
public class Robot extends TimedRobot {
  // created before the robot's first loop, in a static field
  static final SparkMax arm = new SparkMax(1, MotorType.kBrushless);
  private final SparkMax follower = new SparkMax(2, MotorType.kBrushless);
  private final SparkMax orphan = new SparkMax(4, MotorType.kBrushless);
  private SparkMax shooter;  // created lazily, in teleopInit

  // A student-style physics sim, as in REV's examples. Under scrimmage the agent turns its iterate into a no-op.
  private final SparkMaxSim studentSim = new SparkMaxSim(arm, DCMotor.getNEO(2));

  private final Joystick stick = new Joystick(0);
  private int loops = 0;

  public Robot() {
    boolean inverted = "1".equals(System.getenv("PROBE_INVERT"));
    boolean onAbs = "abs".equals(System.getenv("PROBE_FEEDBACK"));
    var cfg = new SparkMaxConfig();
    cfg.idleMode(IdleMode.kBrake).inverted(inverted);
    cfg.encoder.positionConversionFactor(360.0 / 60.0)    // arm degrees (60:1 gearbox)
        .velocityConversionFactor(360.0 / 60.0 / 60.0);  // arm degrees per second
    cfg.absoluteEncoder.inverted(inverted).zeroOffset(0.1)
        .positionConversionFactor(360.0)                  // arm degrees (the encoder is on the arm shaft)
        .velocityConversionFactor(360.0 / 60.0);          // arm degrees per second
    cfg.limitSwitch.forwardLimitSwitchTriggerBehavior(Behavior.kStopMovingMotor);
    cfg.closedLoop.feedbackSensor(onAbs ? FeedbackSensor.kAbsoluteEncoder : FeedbackSensor.kPrimaryEncoder)
        .pid(0.02, 0.0, 0.0).outputRange(-0.5, 0.5);
    if (onAbs) {  // the absolute encoder reads [0, 360): take the short way round, as MAXSwerve's turning does
      cfg.closedLoop.positionWrappingEnabled(true).positionWrappingInputRange(0, 360);
    }
    cfg.closedLoop.maxMotion.cruiseVelocity(90.0).maxAcceleration(180.0);
    report("arm", arm.configure(cfg, ResetMode.kResetSafeParameters, PersistMode.kNoPersistParameters));

    var f = new SparkMaxConfig();
    f.idleMode(IdleMode.kBrake).follow(arm, true);
    report("follower", follower.configure(f, ResetMode.kResetSafeParameters, PersistMode.kNoPersistParameters));
    var o = new SparkMaxConfig();
    o.follow(9);
    report("orphan", orphan.configure(o, ResetMode.kResetSafeParameters, PersistMode.kNoPersistParameters));
  }

  private static void report(String name, Object status) {
    System.out.printf("cfg %s %s%n", name, status);
  }

  @Override
  public void teleopInit() {
    if (shooter == null) {
      shooter = new SparkMax(3, MotorType.kBrushless);
      System.out.println("created SPARK MAX 3");
    }
  }

  @Override
  public void teleopPeriodic() {
    shooter.set(0.5);
    command();
  }

  @Override
  public void disabledPeriodic() {
    command();
  }

  private void command() {
    if (stick.getRawButtonPressed(3)) {
      arm.getEncoder().setPosition(0);
      follower.getEncoder().setPosition(0);
      System.out.printf("zeroed at loop=%d%n", loops);
    }
    double target = stick.getRawAxis(1) * 90.0;
    if (stick.getRawButton(1)) {
      arm.getClosedLoopController().setSetpoint(target, ControlType.kPosition);
    } else if (stick.getRawButton(2)) {
      arm.getClosedLoopController().setSetpoint(target, ControlType.kMAXMotionPositionControl);
    } else {
      arm.set(stick.getRawAxis(0));
    }
  }

  @Override
  public void simulationPeriodic() {
    studentSim.iterate(1000.0, RobotController.getBatteryVoltage(), 0.02);
  }

  @Override
  public void robotPeriodic() {
    if (++loops <= 5 || loops % 5 == 0) {
      System.out.printf("rev loop=%d t=%.4f abs=%.4f rel=%.4f rel2=%.4f app1=%.4f app2=%.4f cur1=%.3f cur2=%.3f"
              + " fwd=%b app3=%.4f app4=%.4f%n",
          loops, Timer.getFPGATimestamp(), arm.getAbsoluteEncoder().getPosition(), arm.getEncoder().getPosition(),
          follower.getEncoder().getPosition(), arm.getAppliedOutput(), follower.getAppliedOutput(),
          arm.getOutputCurrent(), follower.getOutputCurrent(), arm.getForwardLimitSwitch().isPressed(),
          shooter == null ? 0.0 : shooter.getAppliedOutput(), orphan.getAppliedOutput());
    }
  }
}
