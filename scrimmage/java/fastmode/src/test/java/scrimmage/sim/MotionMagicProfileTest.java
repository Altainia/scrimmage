package scrimmage.sim;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class MotionMagicProfileTest {
    private static final double DT = 0.005;
    private static final double EPS = 1e-9;
    private static final int MAX_STEPS = 20_000;

    @Test
    void trapezoidRespectsCruiseAndAccel() {
        MotionMagicProfile.State s = new MotionMagicProfile.State(0, 0, 0);
        int steps = 0;
        while (!(s.position() == 10 && s.velocity() == 0) && steps < MAX_STEPS) {
            s = MotionMagicProfile.next(s, 10, 2, 4, 0, DT);
            assertTrue(s.velocity() <= 2 + EPS, "velocity " + s.velocity());
            assertTrue(Math.abs(s.acceleration()) <= 4 + EPS, "acceleration " + s.acceleration());
            steps++;
        }
        assertEquals(10, s.position(), 1e-6);
        assertEquals(0, s.velocity(), 0);
        // Ideal trapezoid: 0.5 s up, 4.5 s cruise, 0.5 s down = 5.5 s.
        assertEquals(5.5, steps * DT, 2 * DT);
        // Stays put once there.
        MotionMagicProfile.State after = MotionMagicProfile.next(s, 10, 2, 4, 0, DT);
        assertEquals(10, after.position(), 0);
        assertEquals(0, after.velocity(), 0);
    }

    @Test
    void negativeMoveIsMirrored() {
        MotionMagicProfile.State s = new MotionMagicProfile.State(0, 0, 0);
        double minVelocity = 0;
        for (int i = 0; i < MAX_STEPS && !(s.position() == -3 && s.velocity() == 0); i++) {
            s = MotionMagicProfile.next(s, -3, 2, 4, 0, DT);
            minVelocity = Math.min(minVelocity, s.velocity());
        }
        assertEquals(-3, s.position(), 1e-6);
        assertEquals(-2, minVelocity, 1e-9);
    }

    @Test
    void shortMoveIsTriangle() {
        MotionMagicProfile.State s = new MotionMagicProfile.State(0, 0, 0);
        double peak = 0;
        for (int i = 0; i < MAX_STEPS && !(s.position() == 0.1 && s.velocity() == 0); i++) {
            s = MotionMagicProfile.next(s, 0.1, 2, 4, 0, DT);
            peak = Math.max(peak, s.velocity());
        }
        assertTrue(peak < 2 - 0.1, "peak velocity " + peak);
        assertEquals(0.1, s.position(), 1e-6);
    }

    @Test
    void jerkLimitsAccelerationChange() {
        MotionMagicProfile.State s = new MotionMagicProfile.State(0, 0, 0);
        int steps = 0;
        while (!(s.position() == 10 && s.velocity() == 0) && steps < MAX_STEPS) {
            MotionMagicProfile.State n = MotionMagicProfile.next(s, 10, 2, 4, 20, DT);
            assertTrue(Math.abs(n.acceleration() - s.acceleration()) <= 20 * DT + EPS,
                    "jump from " + s.acceleration() + " to " + n.acceleration());
            assertTrue(n.velocity() <= 2 + EPS, "velocity " + n.velocity());
            assertTrue(Math.abs(n.acceleration()) <= 4 + EPS, "acceleration " + n.acceleration());
            assertTrue(n.position() <= 10 + 1e-6, "overshoot " + n.position());
            s = n;
            steps++;
        }
        assertEquals(10, s.position(), 1e-6);
        assertEquals(0, s.velocity(), 0);
        // S-curve takes the trapezoid's 5.5 s plus accel/jerk = 0.2 s.
        assertEquals(5.7, steps * DT, 4 * DT);
    }

    @Test
    void velocityProfileRampsAtAccel() {
        MotionMagicProfile.State s = new MotionMagicProfile.State(0, 0, 0);
        int steps = 0;
        while (s.velocity() != 5 && steps < MAX_STEPS) {
            s = MotionMagicProfile.nextVelocity(s, 5, 10, 0, DT);
            assertTrue(s.velocity() <= 5 + EPS);
            steps++;
        }
        assertEquals(0.5, steps * DT, DT);
        // Position integrates the ramp: 0.5 * 10 * 0.5^2 = 1.25 rot.
        assertEquals(1.25, s.position(), 0.05);
    }

    @Test
    void velocityProfileWithJerkIsSmooth() {
        MotionMagicProfile.State s = new MotionMagicProfile.State(0, 0, 0);
        int steps = 0;
        while (s.velocity() != -5 && steps < MAX_STEPS) {
            MotionMagicProfile.State n = MotionMagicProfile.nextVelocity(s, -5, 10, 100, DT);
            assertTrue(Math.abs(n.acceleration() - s.acceleration()) <= 100 * DT + EPS);
            assertTrue(n.velocity() >= -5 - EPS, "velocity " + n.velocity());
            s = n;
            steps++;
        }
        // 0.5 s at full accel plus accel/jerk = 0.1 s.
        assertEquals(0.6, steps * DT, 2 * DT);
    }
}
