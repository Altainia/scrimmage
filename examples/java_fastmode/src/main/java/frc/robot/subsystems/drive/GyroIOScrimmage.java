package frc.robot.subsystems.drive;

import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.math.util.Units;
import edu.wpi.first.wpilibj.Timer;
import scrimmage.sim.ScrimmageGyro;

/**
 * Gyro IO implementation for scrimmage's fast mode, standing in for {@link GyroIOPigeon2}: the
 * simulated gyro {@code "pigeon"} (bound as {@code "SimGyro:pigeon"}).
 */
public class GyroIOScrimmage implements GyroIO {
  private final ScrimmageGyro gyro = new ScrimmageGyro("pigeon");

  public GyroIOScrimmage() {
    gyro.setYaw(0.0); // as GyroIOPigeon2 does
  }

  @Override
  public void updateInputs(GyroIOInputs inputs) {
    inputs.connected = true;
    inputs.yawPosition = Rotation2d.fromDegrees(gyro.getYawDegrees());
    inputs.yawVelocityRadPerSec = Units.degreesToRadians(gyro.getYawRateDegPerSec());

    // One sample per robot loop, matching ModuleIOScrimmage's odometry samples
    inputs.odometryYawTimestamps = new double[] {Timer.getFPGATimestamp()};
    inputs.odometryYawPositions = new Rotation2d[] {inputs.yawPosition};
  }
}
