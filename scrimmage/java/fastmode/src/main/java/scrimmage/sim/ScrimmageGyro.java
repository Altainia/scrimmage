package scrimmage.sim;

import edu.wpi.first.hal.SimDevice;
import edu.wpi.first.hal.SimDevice.Direction;
import edu.wpi.first.hal.SimDouble;

/**
 * A simulated gyro (a Pigeon 2's yaw), the HAL SimDevice {@code ScrimmageGyro:<name>}. scrimmage writes the yaw
 * (degrees, counter-clockwise positive, unwrapped) and the yaw rate (deg/s, counter-clockwise positive).
 *
 * <p>Where scrimmage isn't running (a RoboRIO, a plain test), it reads 0 (or the yaw last set).
 */
public class ScrimmageGyro implements AutoCloseable {
    static final String TYPE = "ScrimmageGyro";

    private final String name;
    private final SimDevice device;
    private final SimDouble yaw;
    private final SimDouble rate;
    private double offset;

    public ScrimmageGyro(String name) {
        Scrimmage.claim(TYPE, name);
        this.name = name;
        device = Scrimmage.createSimDevice(TYPE, name);
        if (device != null) {
            yaw = device.createDouble("yaw", Direction.kInput, 0);
            rate = device.createDouble("rate", Direction.kInput, 0);
        } else {
            yaw = null;
            rate = null;
        }
    }

    private double rawYaw() {
        return device == null ? 0 : yaw.get();
    }

    /** Yaw, degrees, counter-clockwise positive, unwrapped. */
    public synchronized double getYawDegrees() {
        return rawYaw() + offset;
    }

    /** Yaw rate, degrees per second, counter-clockwise positive. */
    public double getYawRateDegPerSec() {
        return device == null ? 0 : rate.get();
    }

    /** Makes the current yaw read {@code deg}. */
    public synchronized void setYaw(double deg) {
        offset = deg - rawYaw();
    }

    @Override
    public void close() {
        if (device != null) {
            device.close();
        }
        Scrimmage.release(TYPE, name);
    }
}
