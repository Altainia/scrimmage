package scrimmage.sim;

/**
 * Motion Magic profile generation, from CTRE's public description: a trapezoidal profile limited by cruise
 * velocity (rps) and acceleration (rps/s), which becomes an S-curve when jerk (rps/s/s) is positive.
 *
 * <p>The generator is stateless and step-based: each call moves the profile one step from {@code current}. It
 * picks the largest acceleration toward the target (within the acceleration and jerk limits) that still lets the
 * profile stop at the target without exceeding cruise velocity, then lands exactly on the target once it can stop
 * within a step. How the real Talon generates its profile is not documented; this is scrimmage's own choice.
 */
public final class MotionMagicProfile {
    /** A profile setpoint: rotations, rps and rps/s. */
    public record State(double position, double velocity, double acceleration) {}

    private static final int SEARCH_STEPS = 60;
    private static final double TOLERANCE = 1e-9;
    private static final double MAX_SUBSTEP = 0.001;

    private MotionMagicProfile() {}

    /**
     * One step of a position profile toward {@code target}. Jerk 0 (or less) means trapezoidal. The step is
     * computed in substeps of at most 1 ms (the Talon's documented loop period): the S-curve's planner can only
     * switch jerk between substeps, and the overshoot that causes grows with the substep cubed.
     */
    public static State next(State current, double target, double cruise, double accel, double jerk, double dt) {
        cruise = Math.abs(cruise);
        accel = Math.abs(accel);
        if (cruise == 0 || accel == 0 || dt <= 0) {
            return new State(current.position(), 0, 0);
        }
        int substeps = (int) Math.ceil(dt / MAX_SUBSTEP - 1e-9);
        double h = dt / substeps;
        State state = current;
        for (int i = 0; i < substeps; i++) {
            state = substep(state, target, cruise, accel, jerk, h);
        }
        return state;
    }

    private static State substep(State current, double target, double cruise, double accel, double jerk,
            double dt) {
        double d = target - current.position();
        // Work in a frame where the target is ahead (positive direction).
        double s = d != 0 ? Math.signum(d) : (current.velocity() != 0 ? Math.signum(current.velocity()) : 1);
        double dist = s * d;
        double v = s * current.velocity();
        double a = s * current.acceleration();

        Stop stop = stop(v, a, accel, jerk);
        boolean easesOffInStep = jerk <= 0 || Math.abs(a) <= jerk * dt + TOLERANCE;
        if (v >= 0 && stop.time <= dt && easesOffInStep && dist <= v * dt + TOLERANCE) {
            return new State(target, 0, 0);
        }

        double lo = -accel;
        double hi = accel;
        if (jerk > 0) {
            lo = Math.min(accel, Math.max(-accel, a - jerk * dt));
            hi = Math.max(-accel, Math.min(accel, a + jerk * dt));
        }
        double an;
        if (feasible(dist, v, a, hi, cruise, accel, jerk, dt)) {
            an = hi;
        } else if (!feasible(dist, v, a, lo, cruise, accel, jerk, dt)) {
            an = lo;
        } else {
            for (int i = 0; i < SEARCH_STEPS; i++) {
                double mid = (lo + hi) / 2;
                if (feasible(dist, v, a, mid, cruise, accel, jerk, dt)) {
                    lo = mid;
                } else {
                    hi = mid;
                }
            }
            an = lo;
        }
        double dp = stepDistance(v, a, an, jerk, dt);
        double vn = stepVelocity(v, a, an, jerk, dt);
        return new State(current.position() + s * dp, s * vn, s * an);
    }

    /**
     * One step of a velocity profile toward {@code targetVelocity} (Motion Magic Velocity: acceleration and jerk
     * limits; no cruise limit). The position integrates the profile velocity.
     */
    public static State nextVelocity(State current, double targetVelocity, double accel, double jerk, double dt) {
        accel = Math.abs(accel);
        double v0 = current.velocity();
        if (accel == 0 || dt <= 0) {
            return new State(current.position() + v0 * dt, v0, 0);
        }
        double e = targetVelocity - v0;
        double s = e != 0 ? Math.signum(e) : 1;
        double err = s * e;
        double a = s * current.acceleration();

        if (jerk <= 0) {
            boolean arrives = err <= accel * dt + TOLERANCE;
            double an = arrives ? err / dt : accel;
            double vn = arrives ? targetVelocity : v0 + s * an * dt;
            return new State(current.position() + (v0 + vn) / 2 * dt, vn, s * an);
        }
        if (Math.abs(a) <= jerk * dt + TOLERANCE && err <= Math.max(a, 0) * dt + TOLERANCE) {
            return new State(current.position() + (v0 + targetVelocity) / 2 * dt, targetVelocity, 0);
        }
        double lo = Math.min(accel, Math.max(-accel, a - jerk * dt));
        double hi = Math.max(-accel, Math.min(accel, a + jerk * dt));
        double an;
        if (velocityFeasible(err, a, hi, jerk, dt)) {
            an = hi;
        } else if (!velocityFeasible(err, a, lo, jerk, dt)) {
            an = lo;
        } else {
            for (int i = 0; i < SEARCH_STEPS; i++) {
                double mid = (lo + hi) / 2;
                if (velocityFeasible(err, a, mid, jerk, dt)) {
                    lo = mid;
                } else {
                    hi = mid;
                }
            }
            an = lo;
        }
        double dv = (a + an) / 2 * dt;
        double dp = v0 * dt + s * (2 * a + an) * dt * dt / 6;
        return new State(current.position() + dp, v0 + s * dv, s * an);
    }

    /** Whether ending this step at acceleration {@code an} still lets the velocity settle without overshoot. */
    private static boolean velocityFeasible(double err, double a, double an, double jerk, double dt) {
        double remaining = err - (a + an) / 2 * dt;
        double easeOff = an > 0 ? an * an / (2 * jerk) : 0;
        return remaining >= easeOff;
    }

    /** Whether ending this step at acceleration {@code an} keeps under cruise and still stops at the target. */
    private static boolean feasible(double dist, double v, double a, double an, double cruise, double accel,
            double jerk, double dt) {
        double vn = stepVelocity(v, a, an, jerk, dt);
        double easeOff = jerk > 0 && an > 0 ? an * an / (2 * jerk) : 0;
        if (vn + easeOff > cruise) {
            return false;
        }
        return dist - stepDistance(v, a, an, jerk, dt) >= stop(vn, an, accel, jerk).distance;
    }

    /** Distance over one step: constant jerk from {@code a} to {@code an}, or constant {@code an} if no jerk limit. */
    private static double stepDistance(double v, double a, double an, double jerk, double dt) {
        return jerk > 0 ? v * dt + (2 * a + an) * dt * dt / 6 : v * dt + an * dt * dt / 2;
    }

    private static double stepVelocity(double v, double a, double an, double jerk, double dt) {
        return jerk > 0 ? v + (a + an) / 2 * dt : v + an * dt;
    }

    private record Stop(double distance, double time) {}

    /**
     * The quickest stop from velocity {@code v} and acceleration {@code a} (target ahead is positive): its
     * distance and time. When not moving toward the target, the distance is reported as 0 (the real one is
     * not positive, so 0 is the conservative bound).
     */
    private static Stop stop(double v, double a, double accel, double jerk) {
        if (jerk <= 0) {
            return v <= 0 ? new Stop(0, 0) : new Stop(v * v / (2 * accel), v / accel);
        }
        if (v <= 0 && a <= 0) {
            return new Stop(0, 0);
        }
        if (a < 0 && v < a * a / (2 * jerk)) {
            // Already braking harder than needed: the velocity reaches 0 while the braking is still easing off.
            double t = (-a - Math.sqrt(a * a - 2 * jerk * v)) / jerk;
            return new Stop(segment(v, a, jerk, t), t);
        }
        double peak2 = jerk * v + a * a / 2;
        if (peak2 <= 0) {
            return new Stop(0, 0);
        }
        double peak = Math.sqrt(peak2);
        if (peak <= accel) {
            // Ramp down to -peak, then back up to 0 as the velocity reaches 0.
            double t1 = (a + peak) / jerk;
            double t2 = peak / jerk;
            double v1 = v + a * t1 - jerk * t1 * t1 / 2;
            return new Stop(segment(v, a, -jerk, t1) + segment(v1, -peak, jerk, t2), t1 + t2);
        }
        // Ramp down to -accel, hold it, then ramp back up to 0.
        double t1 = (a + accel) / jerk;
        double v1 = v + a * t1 - jerk * t1 * t1 / 2;
        double vRamp = accel * accel / (2 * jerk);
        double th = (v1 - vRamp) / accel;
        double t3 = accel / jerk;
        double distance = segment(v, a, -jerk, t1) + segment(v1, -accel, 0, th) + segment(vRamp, -accel, jerk, t3);
        return new Stop(distance, t1 + th + t3);
    }

    /** Distance covered from velocity {@code v} and acceleration {@code a} under constant jerk {@code j}. */
    private static double segment(double v, double a, double j, double t) {
        return v * t + a * t * t / 2 + j * t * t * t / 6;
    }
}
