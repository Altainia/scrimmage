"""Test robot for scrimmage's RobotPy REV tests (tests/rev_probe_py.toml), the RobotPy twin of tests/rev_probe: an arm
on SPARK MAX 1 (NEO, absolute encoder on its data port, forward limit switch) with SPARK MAX 2 following it inverted,
and a flywheel on SPARK MAX 3 (created lazily in teleopInit) and SPARK MAX 4 (follows CAN id 9, which this program
never creates).

Environment: PROBE_INVERT=1 configures SPARK 1 inverted, and its absolute encoder inverted; PROBE_FEEDBACK=abs closes
the arm's loop on the absolute encoder instead of the built-in encoder; PROBE_SHOOTER=velocity runs the flywheel on a
velocity PID (3000 rpm), which acts on REV's noisy simulated velocity, instead of duty cycle 0.5.

Joystick 0, in teleop and disabled alike (a disabled SPARK ignores what it is sent): axis 0 is the arm's duty cycle;
holding button 1 runs a position PID to axis 1 x 90 (degrees); pressing button 3 zeroes the arm's and the follower's
built-in encoders. Prints one "rev ..." line on each of the first 5 loops, then every 5 loops.
"""

import os

import rev
import wpilib
from wpimath.system.plant import DCMotor

BRUSHLESS = rev.SparkLowLevel.MotorType.kBrushless


def report(name: str, status) -> None:
    print(f"cfg {name} {status}", flush=True)


class MyRobot(wpilib.TimedRobot):
    def robotInit(self) -> None:
        self.arm = rev.SparkMax(1, BRUSHLESS)
        self.follower = rev.SparkMax(2, BRUSHLESS)
        self.orphan = rev.SparkMax(4, BRUSHLESS)
        self.shooter = None  # created lazily, in teleopInit
        # A student-style physics sim, as in REV's examples. Under scrimmage the helper turns its iterate into a no-op.
        self.student_sim = rev.SparkMaxSim(self.arm, DCMotor.NEO(2))
        self.stick = wpilib.Joystick(0)
        self.loops = 0

        inverted = os.environ.get("PROBE_INVERT") == "1"
        on_abs = os.environ.get("PROBE_FEEDBACK") == "abs"
        cfg = rev.SparkMaxConfig()
        cfg.setIdleMode(rev.SparkMaxConfig.IdleMode.kBrake).inverted(inverted)
        cfg.encoder.positionConversionFactor(360.0 / 60.0)          # arm degrees (60:1 gearbox)
        cfg.encoder.velocityConversionFactor(360.0 / 60.0 / 60.0)   # arm degrees per second
        cfg.absoluteEncoder.inverted(inverted).zeroOffset(0.1)
        cfg.absoluteEncoder.positionConversionFactor(360.0)         # arm degrees (the encoder is on the arm shaft)
        cfg.absoluteEncoder.velocityConversionFactor(360.0 / 60.0)  # arm degrees per second
        cfg.limitSwitch.forwardLimitSwitchTriggerBehavior(rev.LimitSwitchConfig.Behavior.kStopMovingMotor)
        sensor = rev.FeedbackSensor.kAbsoluteEncoder if on_abs else rev.FeedbackSensor.kPrimaryEncoder
        cfg.closedLoop.setFeedbackSensor(sensor).pid(0.02, 0.0, 0.0).outputRange(-0.5, 0.5)
        if on_abs:  # the absolute encoder reads [0, 360): take the short way round, as MAXSwerve's turning does
            cfg.closedLoop.positionWrappingEnabled(True).positionWrappingInputRange(0, 360)
        report("arm", self.arm.configure(cfg, rev.ResetMode.kResetSafeParameters,
                                         rev.PersistMode.kNoPersistParameters))

        f = rev.SparkMaxConfig()
        f.setIdleMode(rev.SparkMaxConfig.IdleMode.kBrake).follow(self.arm, True)
        report("follower", self.follower.configure(f, rev.ResetMode.kResetSafeParameters,
                                                   rev.PersistMode.kNoPersistParameters))
        o = rev.SparkMaxConfig()
        o.follow(9)
        report("orphan", self.orphan.configure(o, rev.ResetMode.kResetSafeParameters,
                                               rev.PersistMode.kNoPersistParameters))

    def _make_shooter(self) -> None:
        self.shooter = rev.SparkMax(3, BRUSHLESS)
        print("created SPARK MAX 3", flush=True)
        if os.environ.get("PROBE_SHOOTER") == "velocity":
            c = rev.SparkMaxConfig()
            c.closedLoop.pid(0.0005, 0.0, 0.0)
            report("shooter", self.shooter.configure(c, rev.ResetMode.kResetSafeParameters,
                                                     rev.PersistMode.kNoPersistParameters))

    def teleopInit(self) -> None:
        if self.shooter is None:
            self._make_shooter()

    def teleopPeriodic(self) -> None:
        if os.environ.get("PROBE_SHOOTER") == "velocity":
            self.shooter.getClosedLoopController().setSetpoint(3000.0, rev.SparkLowLevel.ControlType.kVelocity)
        else:
            self.shooter.set(0.5)
        self.command()

    def disabledPeriodic(self) -> None:
        self.command()

    def command(self) -> None:
        if self.stick.getRawButtonPressed(3):
            self.arm.getEncoder().setPosition(0)
            self.follower.getEncoder().setPosition(0)
            print(f"zeroed at loop={self.loops}", flush=True)
        target = self.stick.getRawAxis(1) * 90.0
        if self.stick.getRawButton(1):
            self.arm.getClosedLoopController().setSetpoint(target, rev.SparkLowLevel.ControlType.kPosition)
        else:
            self.arm.set(self.stick.getRawAxis(0))

    def _simulationPeriodic(self) -> None:  # RobotPy's name for simulationPeriodic
        self.student_sim.iterate(1000.0, wpilib.RobotController.getBatteryVoltage(), 0.02)

    def robotPeriodic(self) -> None:
        self.loops += 1
        if self.loops <= 5 or self.loops % 5 == 0:
            a = self.arm
            print(f"rev loop={self.loops} t={wpilib.Timer.getFPGATimestamp():.4f}"
                  f" abs={a.getAbsoluteEncoder().getPosition():.4f} rel={a.getEncoder().getPosition():.4f}"
                  f" rel2={self.follower.getEncoder().getPosition():.4f} app1={a.getAppliedOutput():.4f}"
                  f" app2={self.follower.getAppliedOutput():.4f} cur1={a.getOutputCurrent():.3f}"
                  f" cur2={self.follower.getOutputCurrent():.3f}"
                  f" fwd={'true' if a.getForwardLimitSwitch().get() else 'false'}"
                  f" app3={0.0 if self.shooter is None else self.shooter.getAppliedOutput():.4f}"
                  f" app4={self.orphan.getAppliedOutput():.4f}", flush=True)
