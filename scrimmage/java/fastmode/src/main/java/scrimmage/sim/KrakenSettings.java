package scrimmage.sim;

/**
 * Talon-style settings for a simulated Kraken, as plain numbers (no CTRE config objects). Each field means what
 * CTRE's public documentation says the Talon setting of the same name means. Gains are in units of the output
 * type ({@link OutputType}): volts, or duty cycle, per rotation (kP), per rotation-second (kI), per rps (kD, kV),
 * per rps/s (kA); kS and kG are plain output units.
 *
 * <p>Positions and velocities are mechanism units: rotations and rps after {@code sensorToMechanismRatio}.
 */
public class KrakenSettings implements Cloneable {
    /** How kG is applied: always (elevator), or scaled by cos(mechanism angle) (arm; 0 rotations = horizontal). */
    public enum GravityType { ELEVATOR_STATIC, ARM_COSINE }

    /** The sign kS takes: that of the velocity reference, or that of the closed-loop error. */
    public enum StaticFeedforwardSign { VELOCITY_SIGN, CLOSED_LOOP_SIGN }

    /** The units of closed-loop output (and so of the gains): volts or duty cycle. */
    public enum OutputType { VOLTAGE, DUTY_CYCLE }

    public double kP, kI, kD, kS, kV, kA, kG;
    public GravityType gravityType = GravityType.ELEVATOR_STATIC;
    /**
     * Arm/Cosine only: rotations added to the mechanism position before the cosine (CTRE documents the range as
     * -0.25 to 0.25 rotations; values outside it are held to it).
     */
    public double gravityArmPositionOffset;
    public StaticFeedforwardSign staticFeedforwardSign = StaticFeedforwardSign.VELOCITY_SIGN;
    public OutputType outputType = OutputType.VOLTAGE;
    public boolean continuousWrap;

    /** Motion Magic: rps, rps/s and rps/s/s (jerk 0 = trapezoidal profile). */
    public double motionMagicCruiseVelocity, motionMagicAcceleration, motionMagicJerk;

    public double statorCurrentLimit;
    public boolean statorCurrentLimitEnable;
    public double supplyCurrentLimit;
    public boolean supplyCurrentLimitEnable;

    public double forwardSoftLimitThreshold, reverseSoftLimitThreshold;
    public boolean forwardSoftLimitEnable, reverseSoftLimitEnable;

    public double peakForwardVoltage = 16, peakReverseVoltage = -16;
    public double peakForwardDutyCycle = 1, peakReverseDutyCycle = -1;

    /** True = clockwise positive (CTRE's Clockwise_Positive); false = counter-clockwise positive. */
    public boolean inverted;
    public boolean brake;

    public double sensorToMechanismRatio = 1, rotorToSensorRatio = 1;

    /** An independent copy (every field is a primitive or an enum, so a shallow copy is a full one). */
    public KrakenSettings copy() {
        try {
            return (KrakenSettings) super.clone();
        } catch (CloneNotSupportedException e) {
            throw new AssertionError(e);
        }
    }

    public KrakenSettings withKP(double v) { kP = v; return this; }
    public KrakenSettings withKI(double v) { kI = v; return this; }
    public KrakenSettings withKD(double v) { kD = v; return this; }
    public KrakenSettings withKS(double v) { kS = v; return this; }
    public KrakenSettings withKV(double v) { kV = v; return this; }
    public KrakenSettings withKA(double v) { kA = v; return this; }
    public KrakenSettings withKG(double v) { kG = v; return this; }
    public KrakenSettings withGravityType(GravityType v) { gravityType = v; return this; }
    public KrakenSettings withGravityArmPositionOffset(double v) { gravityArmPositionOffset = v; return this; }
    public KrakenSettings withStaticFeedforwardSign(StaticFeedforwardSign v) { staticFeedforwardSign = v; return this; }
    public KrakenSettings withOutputType(OutputType v) { outputType = v; return this; }
    public KrakenSettings withContinuousWrap(boolean v) { continuousWrap = v; return this; }
    public KrakenSettings withMotionMagicCruiseVelocity(double v) { motionMagicCruiseVelocity = v; return this; }
    public KrakenSettings withMotionMagicAcceleration(double v) { motionMagicAcceleration = v; return this; }
    public KrakenSettings withMotionMagicJerk(double v) { motionMagicJerk = v; return this; }
    public KrakenSettings withStatorCurrentLimit(double v) { statorCurrentLimit = v; return this; }
    public KrakenSettings withStatorCurrentLimitEnable(boolean v) { statorCurrentLimitEnable = v; return this; }
    public KrakenSettings withSupplyCurrentLimit(double v) { supplyCurrentLimit = v; return this; }
    public KrakenSettings withSupplyCurrentLimitEnable(boolean v) { supplyCurrentLimitEnable = v; return this; }
    public KrakenSettings withForwardSoftLimitThreshold(double v) { forwardSoftLimitThreshold = v; return this; }
    public KrakenSettings withReverseSoftLimitThreshold(double v) { reverseSoftLimitThreshold = v; return this; }
    public KrakenSettings withForwardSoftLimitEnable(boolean v) { forwardSoftLimitEnable = v; return this; }
    public KrakenSettings withReverseSoftLimitEnable(boolean v) { reverseSoftLimitEnable = v; return this; }
    public KrakenSettings withPeakForwardVoltage(double v) { peakForwardVoltage = v; return this; }
    public KrakenSettings withPeakReverseVoltage(double v) { peakReverseVoltage = v; return this; }
    public KrakenSettings withPeakForwardDutyCycle(double v) { peakForwardDutyCycle = v; return this; }
    public KrakenSettings withPeakReverseDutyCycle(double v) { peakReverseDutyCycle = v; return this; }
    public KrakenSettings withInverted(boolean v) { inverted = v; return this; }
    public KrakenSettings withBrake(boolean v) { brake = v; return this; }
    public KrakenSettings withSensorToMechanismRatio(double v) { sensorToMechanismRatio = v; return this; }
    public KrakenSettings withRotorToSensorRatio(double v) { rotorToSensorRatio = v; return this; }
}
