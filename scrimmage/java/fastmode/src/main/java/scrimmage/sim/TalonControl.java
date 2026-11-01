package scrimmage.sim;

/**
 * Talon-style control for a simulated Kraken, built from CTRE's public documentation of the Talon FX's
 * closed-loop requests and Motion Magic. CTRE does not document the output formula, the error units or how kI
 * is handled; scrimmage's choice is a standard PID plus feedforward, with errors in mechanism rotations (position)
 * or rps (velocity), and kI integrating error x dt:
 *
 * <pre>out = kS*sign + kV*vRef + kA*aRef + kG*(1 or cos(2*pi*position)) + kP*e + kI*sum(e*dt) + kD*de/dt + feedforward</pre>
 *
 * <p>Output limits apply in this order: peak output, the supply voltage (a motor can't be driven harder than its
 * supply: physics, not a documented Talon behavior), soft limits, stator current limit, supply current limit.
 * Mechanism units are rotations and rps after the ratios; inversion is the device's job.
 *
 * <p>The integrator, derivative and Motion Magic profile restart whenever the request's kind changes.
 */
public class TalonControl {
    private final KrakenSettings settings;
    private ControlRequest.Kind lastKind;
    private double integral;
    private double previousError = Double.NaN;
    private MotionMagicProfile.State profile;
    private double statorCurrent;
    private double supplyCurrent;

    public TalonControl(KrakenSettings settings) {
        this.settings = settings;
    }

    /**
     * Runs one control step.
     *
     * @param r the request
     * @param mechPosition mechanism position (rotations)
     * @param mechVelocity mechanism velocity (rps)
     * @param motorRps rotor velocity (rps), for the current model
     * @param vbus supply voltage (V)
     * @param dt time since the last step (s)
     * @return the voltage to apply across the motor
     */
    public double update(ControlRequest r, double mechPosition, double mechVelocity, double motorRps, double vbus,
            double dt) {
        if (r.kind() != lastKind) {
            reset();
            lastKind = r.kind();
        }
        double volts;
        switch (r.kind()) {
            case NEUTRAL -> {
                // Brake mode shorts the windings, so current flows from back-EMF alone; coast carries none.
                statorCurrent = settings.brake ? KrakenModel.statorCurrent(0, motorRps) : 0;
                supplyCurrent = 0;
                return 0;
            }
            case VOLTAGE -> volts = clampVolts(r.value());
            case DUTY_CYCLE -> volts = clampDuty(r.value()) * vbus;
            default -> volts = closedLoop(r, mechPosition, mechVelocity, vbus, dt);
        }
        volts = capAtSupply(volts, vbus);
        volts = applySoftLimits(volts, mechPosition);
        return applyCurrentLimits(volts, motorRps, vbus);
    }

    /** Stator current (A) after the last update. */
    public double statorCurrent() {
        return statorCurrent;
    }

    /** Supply current (A) after the last update: stator current x |V| / vbus. */
    public double supplyCurrent() {
        return supplyCurrent;
    }

    /** Clears the integrator, the derivative history and any Motion Magic profile. */
    public void reset() {
        lastKind = null;
        integral = 0;
        previousError = Double.NaN;
        profile = null;
    }

    private double closedLoop(ControlRequest r, double position, double velocity, double vbus, double dt) {
        KrakenSettings s = settings;
        double error;
        double vRef = 0;
        double aRef = 0;
        switch (r.kind()) {
            case POSITION -> error = positionError(r.value(), position);
            case VELOCITY -> {
                vRef = r.value();
                error = vRef - velocity;
            }
            case MOTION_MAGIC -> {
                if (profile == null) {
                    profile = new MotionMagicProfile.State(position, velocity, 0);
                }
                double target = s.continuousWrap
                        ? profile.position() + wrap(r.value() - profile.position())
                        : r.value();
                profile = MotionMagicProfile.next(profile, target, s.motionMagicCruiseVelocity,
                        s.motionMagicAcceleration, s.motionMagicJerk, dt);
                error = positionError(profile.position(), position);
                vRef = profile.velocity();
                aRef = profile.acceleration();
            }
            case MOTION_MAGIC_VELOCITY -> {
                if (profile == null) {
                    profile = new MotionMagicProfile.State(position, velocity, 0);
                }
                profile = MotionMagicProfile.nextVelocity(profile, r.value(), s.motionMagicAcceleration,
                        s.motionMagicJerk, dt);
                vRef = profile.velocity();
                aRef = profile.acceleration();
                error = vRef - velocity;
            }
            default -> throw new IllegalArgumentException("not a closed-loop request: " + r.kind());
        }
        integral += error * dt;
        double derivative = Double.isNaN(previousError) || dt <= 0 ? 0 : (error - previousError) / dt;
        previousError = error;

        double sign = s.staticFeedforwardSign == KrakenSettings.StaticFeedforwardSign.VELOCITY_SIGN
                ? Math.signum(vRef)
                : Math.signum(error);
        double gravity = s.gravityType == KrakenSettings.GravityType.ARM_COSINE
                ? Math.cos(2 * Math.PI * (position + Math.max(-0.25, Math.min(0.25, s.gravityArmPositionOffset))))
                : 1;
        double out = s.kS * sign + s.kV * vRef + s.kA * aRef + s.kG * gravity
                + s.kP * error + s.kI * integral + s.kD * derivative;

        if (s.outputType == KrakenSettings.OutputType.VOLTAGE) {
            return clampVolts(out + r.feedforwardVolts());
        }
        double feedforwardDuty = vbus > 0 ? r.feedforwardVolts() / vbus : 0;
        return clampDuty(out + feedforwardDuty) * vbus;
    }

    /** Target minus position; with continuous wrap, the shortest way round one rotation. */
    private double positionError(double target, double position) {
        double e = target - position;
        return settings.continuousWrap ? wrap(e) : e;
    }

    /** Wraps rotations into [-0.5, 0.5). */
    private static double wrap(double rotations) {
        return rotations - Math.floor(rotations + 0.5);
    }

    private double clampVolts(double volts) {
        return Math.max(settings.peakReverseVoltage, Math.min(settings.peakForwardVoltage, volts));
    }

    /** Caps the output at ±vbus: no motor controller can apply more than its supply. */
    private static double capAtSupply(double volts, double vbus) {
        double cap = Math.max(0, vbus);
        return Math.max(-cap, Math.min(cap, volts));
    }

    private double clampDuty(double duty) {
        return Math.max(settings.peakReverseDutyCycle, Math.min(settings.peakForwardDutyCycle, duty));
    }

    private double applySoftLimits(double volts, double position) {
        if (settings.forwardSoftLimitEnable && position > settings.forwardSoftLimitThreshold && volts > 0) {
            return 0;
        }
        if (settings.reverseSoftLimitEnable && position < settings.reverseSoftLimitThreshold && volts < 0) {
            return 0;
        }
        return volts;
    }

    /**
     * Reduces output to respect the stator and supply limits. CTRE documents that the limits reduce output; how
     * exactly is not documented. Here the voltage is set so the Kraken model's current sits at the limit.
     */
    private double applyCurrentLimits(double volts, double motorRps, double vbus) {
        double stator = KrakenModel.statorCurrent(volts, motorRps);
        if (settings.statorCurrentLimitEnable && Math.abs(stator) > settings.statorCurrentLimit) {
            volts = KrakenModel.voltsForStatorCurrent(Math.copySign(settings.statorCurrentLimit, stator), motorRps);
            stator = KrakenModel.statorCurrent(volts, motorRps);
        }
        double supply = supplyFromStator(stator, volts, vbus);
        boolean motoring = volts * stator > 0;
        if (settings.supplyCurrentLimitEnable && motoring && Math.abs(supply) > settings.supplyCurrentLimit) {
            // Solve |V| * |I(V)| = limit * vbus, with I = (V - backEmf) / R, for |V|.
            double sign = Math.signum(volts);
            double b = sign * KrakenModel.backEmf(motorRps);
            double r = KrakenModel.resistance();
            double magnitude = (b + Math.sqrt(b * b + 4 * r * settings.supplyCurrentLimit * vbus)) / 2;
            volts = sign * magnitude;
            stator = KrakenModel.statorCurrent(volts, motorRps);
            supply = supplyFromStator(stator, volts, vbus);
        }
        statorCurrent = stator;
        supplyCurrent = supply;
        return volts;
    }

    private static double supplyFromStator(double stator, double volts, double vbus) {
        return vbus > 0 ? stator * Math.abs(volts) / vbus : 0;
    }
}
