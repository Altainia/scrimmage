package scrimmage.sim;

import edu.wpi.first.hal.SimDevice;
import edu.wpi.first.hal.SimDevice.Direction;
import edu.wpi.first.hal.SimDouble;

/**
 * A simulated CANcoder, the HAL SimDevice {@code ScrimmageEncoder:<name>}. scrimmage writes the magnet's position
 * (rotations, counter-clockwise positive in the frame of CTRE's CounterClockwise_Positive sensor direction,
 * unwrapped) and velocity (rps). As CTRE documents for the CANcoder, the reading is that position in the
 * configured sensor direction plus the magnet offset; the absolute position is the same, wrapped to [-0.5, 0.5).
 *
 * <p>Where scrimmage isn't running (a RoboRIO, a plain test), it reads 0.
 */
public class ScrimmageCANcoder implements AutoCloseable {
    static final String TYPE = "ScrimmageEncoder";

    private final String name;
    private final double magnetOffset;
    private final double sign;
    private final SimDevice device;
    private final SimDouble position;
    private final SimDouble velocity;

    /**
     * @param name the device name (scrimmage's config binds {@code ScrimmageEncoder:<name>})
     * @param magnetOffsetRot CTRE's MagnetOffset, rotations
     * @param clockwisePositive CTRE's SensorDirection Clockwise_Positive (false = CounterClockwise_Positive)
     */
    public ScrimmageCANcoder(String name, double magnetOffsetRot, boolean clockwisePositive) {
        Scrimmage.claim(TYPE, name);
        this.name = name;
        this.magnetOffset = magnetOffsetRot;
        this.sign = clockwisePositive ? -1 : 1;
        device = Scrimmage.createSimDevice(TYPE, name);
        if (device != null) {
            position = device.createDouble("position", Direction.kInput, 0);
            velocity = device.createDouble("velocity", Direction.kInput, 0);
        } else {
            position = null;
            velocity = null;
        }
    }

    /** Absolute position, rotations in [-0.5, 0.5). */
    public double getAbsolutePosition() {
        double p = getPosition();
        return p - Math.floor(p + 0.5);
    }

    /** Position, rotations (continuous; starts at the absolute position). */
    public double getPosition() {
        return device == null ? 0 : sign * position.get() + magnetOffset;
    }

    /** Velocity, rps. */
    public double getVelocity() {
        return device == null ? 0 : sign * velocity.get();
    }

    @Override
    public void close() {
        if (device != null) {
            device.close();
        }
        Scrimmage.release(TYPE, name);
    }
}
