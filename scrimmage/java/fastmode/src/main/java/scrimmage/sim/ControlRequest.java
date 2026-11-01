package scrimmage.sim;

/**
 * What a simulated Kraken is asked to do, Talon-style. Units are mechanism units (after the ratios): rotations,
 * rotations per second, volts, or duty cycle in [-1, 1].
 *
 * <p>{@code feedforwardVolts} is an extra term added to closed-loop requests (position, velocity, Motion Magic);
 * open-loop and neutral requests ignore it.
 */
public record ControlRequest(Kind kind, double value, double feedforwardVolts) {
    public enum Kind {
        VOLTAGE,
        DUTY_CYCLE,
        POSITION,
        VELOCITY,
        MOTION_MAGIC,
        MOTION_MAGIC_VELOCITY,
        NEUTRAL
    }

    public static ControlRequest voltage(double volts) {
        return new ControlRequest(Kind.VOLTAGE, volts, 0);
    }

    public static ControlRequest dutyCycle(double duty) {
        return new ControlRequest(Kind.DUTY_CYCLE, duty, 0);
    }

    public static ControlRequest position(double rotations) {
        return new ControlRequest(Kind.POSITION, rotations, 0);
    }

    public static ControlRequest velocity(double rps) {
        return new ControlRequest(Kind.VELOCITY, rps, 0);
    }

    public static ControlRequest motionMagic(double rotations) {
        return new ControlRequest(Kind.MOTION_MAGIC, rotations, 0);
    }

    public static ControlRequest motionMagicVelocity(double rps) {
        return new ControlRequest(Kind.MOTION_MAGIC_VELOCITY, rps, 0);
    }

    public static ControlRequest neutral() {
        return new ControlRequest(Kind.NEUTRAL, 0, 0);
    }

    /** The same request with an added feedforward, in volts. */
    public ControlRequest withFeedforward(double volts) {
        return new ControlRequest(kind, value, volts);
    }
}
