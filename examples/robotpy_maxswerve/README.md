# RobotPy MAXSwerve example

This is robotpy-rev's MAXSwerve example, a RobotPy port of REV's MAXSwerve template: four MAXSwerve modules on
SPARK MAX controllers (NEO drive motors, NEO 550 steer motors, a Through Bore absolute encoder on each steer SPARK) and
an ADIS16448 IMU. Scrimmage runs it with `configs/maxswerve_py.toml`:

    scrimmage run scenarios/maxswerve_py_auto.toml

It needs `robotpy-rev` and `commands2` (`pip install -e ".[robotpy]"` in Scrimmage installs both).

## Where it comes from

- Repository: https://github.com/robotpy/robotpy-rev, folder `examples/maxswerve`
- Tag `2026.0.4`, commit `f6085a73fd1295b4cc5bc8fa51e6d3761c93c831` (the repository's default branch has moved on to
  RobotPy 2027, which this version of Scrimmage doesn't use)
- Licence: `LICENSE` is the repository's (RobotPy pieces Apache-2.0, REV pieces BSD-3-Clause); `WPILib-License.md` is
  the WPILib licence the file headers refer to.

## The one change

The example is copied as published except for one fix in `subsystems/maxswervemodule.py`. As published it stops in
`robotInit` with `AttributeError: type object 'rev._rev.ClosedLoopConfig' has no attribute 'FeedbackSensor'`:
REVLib 2026 moved that enum from `ClosedLoopConfig.FeedbackSensor` to `rev.FeedbackSensor`, and the example wasn't
updated (robotpy-rev's own example tests skip it). The fix uses the new name:

    -from rev import SparkMax, SparkMaxConfig, ClosedLoopConfig, ResetMode, PersistMode
    +from rev import SparkMax, SparkMaxConfig, ClosedLoopConfig, ResetMode, PersistMode, FeedbackSensor
    ...
    -            ClosedLoopConfig.FeedbackSensor.kPrimaryEncoder
    +            FeedbackSensor.kPrimaryEncoder
    ...
    -            ClosedLoopConfig.FeedbackSensor.kAbsoluteEncoder
    +            FeedbackSensor.kAbsoluteEncoder

A real robot running the published code would crash the same way. Scrimmage itself never needs robot-code changes.

## Why it doesn't drive the whole path

The autonomous routine follows an S-curve that should end 3 m straight ahead. Under Scrimmage the robot drives only
about 1.57 m, with a top speed of about 0.69 m/s (the path asks for 3 m/s), and the S is much flatter than planned.

That is what this code would do on a real robot with REVLib 2026, so Scrimmage shows it as is. The drive SPARKs'
velocity feedforward is set with `closedLoop.velocityFF(1 / kDriveWheelFreeSpeedRps)`, about 0.2. Older REVLib read
that number as duty cycle (fraction of full output) per m/s. REVLib 2026 reads it as the feedforward `kV` in **volts**
per m/s, so the feedforward is 12 times too weak, and the small P gain (0.04) can't make up the difference. REV's Java
template was updated to `feedForward.kV(nominalVoltage / kDriveWheelFreeSpeedRps)` (12 / free speed); this example
wasn't.

The simulation isn't hiding a different problem: the robot's own odometry agrees with Scrimmage's ground truth to
about 1 cm at the end of the run. To see the full path, change the feedforward to `12 / kDriveWheelFreeSpeedRps` in
your own copy.
