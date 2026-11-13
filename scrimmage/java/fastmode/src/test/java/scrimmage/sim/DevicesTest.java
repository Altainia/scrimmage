package scrimmage.sim;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import edu.wpi.first.hal.HAL;
import edu.wpi.first.hal.SimDevice;
import edu.wpi.first.hal.DriverStationJNI;
import edu.wpi.first.hal.simulation.DriverStationDataJNI;
import edu.wpi.first.wpilibj.simulation.SimDeviceSim;
import edu.wpi.first.wpilibj.simulation.SimHooks;
import java.lang.management.ManagementFactory;
import java.lang.management.MonitorInfo;
import java.lang.management.ThreadInfo;
import java.lang.management.ThreadMXBean;
import java.util.Map;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class DevicesTest {
    private static final double EPS = 1e-9;
    private static boolean hal;
    private static String halError;
    /** The time the devices see (µs); tests move it themselves. */
    private static long now = 1_000_000;

    @BeforeAll
    static void initHal() {
        try {
            hal = HAL.initialize(500, 0);
            halError = "HAL.initialize returned false";
        } catch (LinkageError e) {
            hal = false;
            halError = e.toString();
        }
        if (hal) {
            SimHooks.pauseTiming(); // the shared notifier only runs when a test steps the clock
        }
    }

    @BeforeEach
    void enableRobot() {
        ScrimmageKraken.clock = () -> now;
        if (hal) {
            setEnabled(true);
        }
    }

    /**
     * One control tick, 5 ms after the robot code that came before it, then 1 µs on: as the next robot loop would
     * be, so readings show this tick.
     */
    private static void step() {
        now += 5000;
        ScrimmageKraken.updateAll(now, ScrimmageKraken.PERIOD);
        now += 1;
    }

    private static void requireHal() {
        assumeTrue(hal, () -> "skipped: WPILib's HAL desktop natives (hal-cpp/wpiutil-cpp linuxx86-64) aren't "
                + "available to this test JVM (" + halError + ")");
    }

    /** Enables or disables the robot as the robot program would see it (HAL-level: no NetworkTables needed). */
    private static void setEnabled(boolean enabled) {
        DriverStationDataJNI.setEnabled(enabled);
        DriverStationDataJNI.setDsAttached(true);
        DriverStationDataJNI.notifyNewData();
        DriverStationJNI.refreshDSData();
    }

    private static double out(String device, String value) {
        return new SimDeviceSim(device).getDouble(value).get();
    }

    private static void in(String device, String value, double v) {
        new SimDeviceSim(device).getDouble(value).set(v);
    }

    @Test
    void fastModeFollowsEnvironment() {
        assertFalse(Scrimmage.fastMode(Map.of()));
        assertFalse(Scrimmage.fastMode(Map.of("SCRIMMAGE_MODE", "phoenix")));
        assertFalse(Scrimmage.fastMode(Map.of("SCRIMMAGE_MODE", "FAST")));
        assertTrue(Scrimmage.fastMode(Map.of("SCRIMMAGE_MODE", "fast")));
    }

    @Test
    void duplicateNameThrows() {
        try (ScrimmageKraken first = new ScrimmageKraken("elevator", new KrakenSettings())) {
            IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                    () -> new ScrimmageKraken("elevator", new KrakenSettings()));
            assertTrue(e.getMessage().contains("elevator"), e.getMessage());
        }
        // Closing frees the name.
        new ScrimmageKraken("elevator", new KrakenSettings()).close();
    }

    @Test
    void duplicateEncoderAndGyroNamesThrow() {
        try (ScrimmageCANcoder e = new ScrimmageCANcoder("fl", 0, false); ScrimmageGyro g = new ScrimmageGyro("pigeon")) {
            assertThrows(IllegalArgumentException.class, () -> new ScrimmageCANcoder("fl", 0, false));
            assertThrows(IllegalArgumentException.class, () -> new ScrimmageGyro("pigeon"));
        }
    }

    @Test
    void constructsWithoutScrimmage() {
        // With HAL sim active, a name the HAL already holds makes SimDevice.create return null, as it always does
        // on a RoboRIO. Without the natives (a plain test), creating the SimDevice can't link at all.
        SimDevice m = null, e = null, g = null;
        if (hal) {
            m = SimDevice.create("ScrimmageMotor:held");
            e = SimDevice.create("ScrimmageEncoder:held");
            g = SimDevice.create("ScrimmageGyro:held");
        }
        try (ScrimmageKraken k = new ScrimmageKraken("held", new KrakenSettings().withSensorToMechanismRatio(3));
                ScrimmageCANcoder c = new ScrimmageCANcoder("held", 0.25, true);
                ScrimmageGyro y = new ScrimmageGyro("held")) {
            k.setControl(ControlRequest.voltage(5));
            k.useRemoteEncoder(c);
            step();
            assertEquals(0, k.getPosition());
            assertEquals(0, k.getVelocity());
            assertEquals(0, k.getMotorVoltage());
            assertEquals(0, k.getStatorCurrent());
            assertEquals(0, k.getSupplyCurrent());
            assertEquals(0, c.getPosition());
            assertEquals(0, c.getVelocity());
            assertEquals(0, c.getAbsolutePosition());
            assertEquals(0, y.getYawDegrees());
            assertEquals(0, y.getYawRateDegPerSec());
        } finally {
            for (SimDevice d : new SimDevice[] {m, e, g}) {
                if (d != null) {
                    d.close();
                }
            }
        }
    }

    @Test
    void followerOpposedMirrorsLeader() {
        requireHal();
        try (ScrimmageKraken leader = new ScrimmageKraken("leader", new KrakenSettings());
                ScrimmageKraken follower = new ScrimmageKraken("follower", new KrakenSettings())) {
            follower.follow(leader, true);
            leader.setControl(ControlRequest.voltage(6));
            step();
            assertEquals(6, out("ScrimmageMotor:leader", "motorVoltage"), EPS);
            assertEquals(-6, out("ScrimmageMotor:follower", "motorVoltage"), EPS);
        }
    }

    @Test
    void followerOfAClosedLeaderGoesNeutral() {
        requireHal();
        try (ScrimmageKraken follower = new ScrimmageKraken("follower", new KrakenSettings())) {
            ScrimmageKraken leader = new ScrimmageKraken("leader", new KrakenSettings());
            follower.follow(leader, false);
            leader.setControl(ControlRequest.voltage(6));
            step();
            assertEquals(6, out("ScrimmageMotor:follower", "motorVoltage"), EPS);
            leader.close();
            step();
            assertEquals(0, out("ScrimmageMotor:follower", "motorVoltage"), EPS);
            assertEquals(0, follower.getMotorVoltage(), EPS);
        }
    }

    @Test
    void aFailedConstructorLeavesTheNameFree() {
        assertThrows(NullPointerException.class, () -> new ScrimmageKraken("retry", null));
        new ScrimmageKraken("retry", new KrakenSettings()).close();
    }

    @Test
    void followCallsOnTwoThreadsCantMakeALoop() throws Exception {
        try (ScrimmageKraken a = new ScrimmageKraken("a", new KrakenSettings());
                ScrimmageKraken b = new ScrimmageKraken("b", new KrakenSettings())) {
            ExecutorService pool = Executors.newFixedThreadPool(2);
            try {
                for (int i = 0; i < 2000; i++) {
                    a.setControl(ControlRequest.neutral());
                    b.setControl(ControlRequest.neutral());
                    CyclicBarrier together = new CyclicBarrier(2);
                    Future<?> ab = pool.submit(() -> followAfter(together, a, b));
                    Future<?> ba = pool.submit(() -> followAfter(together, b, a));
                    ab.get();
                    ba.get();
                    assertFalse(a.leader() == b && b.leader() == a, "a loop on round " + i);
                }
            } finally {
                pool.shutdownNow();
            }
        }
    }

    /** {@code follower.follow(leader)} once both threads are ready; a refused loop is fine. */
    private static Void followAfter(CyclicBarrier together, ScrimmageKraken follower, ScrimmageKraken leader)
            throws Exception {
        together.await();
        try {
            follower.follow(leader, false);
        } catch (IllegalArgumentException refused) {
            // the other thread got there first
        }
        return null;
    }

    @Test
    void followerUpdatesAfterItsLeaderEvenWhenCreatedFirst() {
        requireHal();
        try (ScrimmageKraken follower = new ScrimmageKraken("early", new KrakenSettings());
                ScrimmageKraken leader = new ScrimmageKraken("late", new KrakenSettings())) {
            follower.follow(leader, false);
            leader.setControl(ControlRequest.voltage(4));
            step(); // one step: the follower must see this step's leader output
            assertEquals(4, out("ScrimmageMotor:early", "motorVoltage"), EPS);
        }
    }

    @Test
    void invertedFlipsVoltageAndReadings() {
        requireHal();
        KrakenSettings s = new KrakenSettings().withInverted(true).withSensorToMechanismRatio(4);
        try (ScrimmageKraken k = new ScrimmageKraken("inv", s)) {
            k.setControl(ControlRequest.voltage(3));
            step();
            assertEquals(-3, out("ScrimmageMotor:inv", "motorVoltage"), EPS);
            assertEquals(3, k.getMotorVoltage(), EPS);
            in("ScrimmageMotor:inv", "position", 2.0);
            in("ScrimmageMotor:inv", "velocity", 8.0);
            assertEquals(-2.0 / 4, k.getPosition(), EPS);
            assertEquals(-8.0 / 4, k.getVelocity(), EPS);
        }
    }

    @Test
    void settingsAreCopiedAtConstruction() {
        requireHal();
        KrakenSettings s = new KrakenSettings().withSensorToMechanismRatio(2);
        try (ScrimmageKraken left = new ScrimmageKraken("left", s);
                ScrimmageKraken right = new ScrimmageKraken("right", s.withInverted(true).withBrake(true))) {
            s.withSensorToMechanismRatio(10);
            left.setControl(ControlRequest.voltage(3));
            right.setControl(ControlRequest.voltage(3));
            step();
            assertEquals(3, out("ScrimmageMotor:left", "motorVoltage"), EPS);
            assertFalse(new SimDeviceSim("ScrimmageMotor:left").getBoolean("brake").get());
            assertEquals(-3, out("ScrimmageMotor:right", "motorVoltage"), EPS);
            in("ScrimmageMotor:left", "position", 4.0);
            assertEquals(2.0, left.getPosition(), EPS);
            in("ScrimmageMotor:right", "position", 4.0);
            assertEquals(-2.0, right.getPosition(), EPS);
        }
    }

    @Test
    void publishesBrake() {
        requireHal();
        try (ScrimmageKraken k = new ScrimmageKraken("braked", new KrakenSettings().withBrake(true))) {
            step();
            assertTrue(new SimDeviceSim("ScrimmageMotor:braked").getBoolean("brake").get());
        }
    }

    @Test
    void setPositionResetsTheReading() {
        requireHal();
        try (ScrimmageKraken k = new ScrimmageKraken("reset", new KrakenSettings().withSensorToMechanismRatio(2))) {
            in("ScrimmageMotor:reset", "position", 10.0);
            k.setPosition(1.0);
            assertEquals(1.0, k.getPosition(), EPS);
            in("ScrimmageMotor:reset", "position", 12.0);
            assertEquals(2.0, k.getPosition(), EPS);
        }
    }

    @Test
    void positionLoopUsesMechanismUnits() {
        requireHal();
        KrakenSettings s = new KrakenSettings().withKP(10).withSensorToMechanismRatio(5);
        try (ScrimmageKraken k = new ScrimmageKraken("pos", s)) {
            in("ScrimmageMotor:pos", "position", 5.0); // 1 mechanism rotation
            k.setControl(ControlRequest.position(1.5));
            step();
            assertEquals(5.0, out("ScrimmageMotor:pos", "motorVoltage"), EPS);
        }
    }

    @Test
    void remoteEncoderDrivesTheLoop() {
        requireHal();
        KrakenSettings s = new KrakenSettings().withKP(10).withSensorToMechanismRatio(1).withRotorToSensorRatio(12.8);
        try (ScrimmageCANcoder e = new ScrimmageCANcoder("steer", 0.1, false);
                ScrimmageKraken k = new ScrimmageKraken("steer", s)) {
            k.useRemoteEncoder(e);
            in("ScrimmageEncoder:steer", "position", 0.2);
            in("ScrimmageMotor:steer", "position", 99.0); // the rotor doesn't set the position
            in("ScrimmageMotor:steer", "velocity", 25.6);
            assertEquals(0.3, k.getPosition(), EPS);
            assertEquals(2.0, k.getVelocity(), EPS); // rotor rps / rotorToSensorRatio / sensorToMechanismRatio
            k.setControl(ControlRequest.position(0.4));
            step();
            assertEquals(1.0, out("ScrimmageMotor:steer", "motorVoltage"), EPS);
        }
    }

    @Test
    void aRemoteCancoderNotFusedGivesTheVelocityToo() {
        requireHal();
        KrakenSettings s = new KrakenSettings().withSensorToMechanismRatio(2).withRotorToSensorRatio(12.8);
        try (ScrimmageCANcoder e = new ScrimmageCANcoder("remote", 0, true);
                ScrimmageKraken k = new ScrimmageKraken("remote", s)) {
            k.useRemoteEncoder(e, false);
            in("ScrimmageEncoder:remote", "position", 0.4);
            in("ScrimmageEncoder:remote", "velocity", 3.0);
            in("ScrimmageMotor:remote", "velocity", 99.0); // the rotor is unused
            assertEquals(-0.2, k.getPosition(), EPS);   // clockwise positive: -0.4 sensor rotations / 2
            assertEquals(-1.5, k.getVelocity(), EPS);   // and the CANcoder's velocity, the same way
            k.useRemoteEncoder(e, true);                 // fused: velocity from the rotor
            assertEquals(99.0 / 12.8 / 2, k.getVelocity(), EPS);
        }
    }

    @Test
    void cancoderAppliesOffsetAndDirection() {
        requireHal();
        try (ScrimmageCANcoder ccw = new ScrimmageCANcoder("ccw", 0.25, false);
                ScrimmageCANcoder cw = new ScrimmageCANcoder("cw", 0.25, true)) {
            in("ScrimmageEncoder:ccw", "position", 1.4);
            in("ScrimmageEncoder:ccw", "velocity", 2.0);
            in("ScrimmageEncoder:cw", "position", 1.4);
            in("ScrimmageEncoder:cw", "velocity", 2.0);
            assertEquals(1.65, ccw.getPosition(), EPS);
            assertEquals(-0.35, ccw.getAbsolutePosition(), EPS);
            assertEquals(2.0, ccw.getVelocity(), EPS);
            assertEquals(-1.15, cw.getPosition(), EPS);
            assertEquals(-0.15, cw.getAbsolutePosition(), EPS);
            assertEquals(-2.0, cw.getVelocity(), EPS);
            in("ScrimmageEncoder:ccw", "position", 0.25); // exactly +0.5 wraps to -0.5
            assertEquals(-0.5, ccw.getAbsolutePosition(), EPS);
        }
    }

    @Test
    void gyroReadsAndResetsYaw() {
        requireHal();
        try (ScrimmageGyro g = new ScrimmageGyro("gyro")) {
            in("ScrimmageGyro:gyro", "yaw", 400.0);
            in("ScrimmageGyro:gyro", "rate", -30.0);
            assertEquals(400.0, g.getYawDegrees(), EPS);
            assertEquals(-30.0, g.getYawRateDegPerSec(), EPS);
            g.setYaw(90);
            assertEquals(90.0, g.getYawDegrees(), EPS);
            in("ScrimmageGyro:gyro", "yaw", 410.0);
            assertEquals(100.0, g.getYawDegrees(), EPS);
        }
    }

    @Test
    void canrangeFloorsToMillimeters() {
        requireHal();
        try (ScrimmageCANrange r = new ScrimmageCANrange("range")) {
            assertEquals(0.0, r.getDistanceMeters(), 0);
            in("ScrimmageCANrange:range", "distance", 0.24996);
            assertEquals(0.249, r.getDistanceMeters(), 1e-12);
            in("ScrimmageCANrange:range", "distance", 3.0);
            assertEquals(3.0, r.getDistanceMeters(), 1e-12);
        }
    }

    @Test
    void canrangeDetectionLatches() {
        requireHal();
        try (ScrimmageCANrange r = new ScrimmageCANrange("latch")) {
            assertTrue(r.getIsDetected()); // distance 0 at boot
            in("ScrimmageCANrange:latch", "distance", 0.40);
            assertTrue(r.getIsDetected());
            in("ScrimmageCANrange:latch", "distance", 0.41);
            assertTrue(r.getIsDetected()); // not above 0.41
            in("ScrimmageCANrange:latch", "distance", 0.42);
            assertFalse(r.getIsDetected());
            in("ScrimmageCANrange:latch", "distance", 0.40);
            assertFalse(r.getIsDetected()); // stays off inside the band
            in("ScrimmageCANrange:latch", "distance", 0.39);
            assertFalse(r.getIsDetected());
            in("ScrimmageCANrange:latch", "distance", 0.38);
            assertTrue(r.getIsDetected());
            in("ScrimmageCANrange:latch", "distance", 0.40);
            assertTrue(r.getIsDetected()); // stays on inside the band
        }
    }

    @Test
    void canrangeCustomProximity() {
        requireHal();
        try (ScrimmageCANrange r = new ScrimmageCANrange("custom")) {
            r.setProximityThreshold(1.0);
            r.setProximityHysteresis(0.1);
            in("ScrimmageCANrange:custom", "distance", 1.2);
            assertFalse(r.getIsDetected());
            in("ScrimmageCANrange:custom", "distance", 1.0);
            assertFalse(r.getIsDetected());
            in("ScrimmageCANrange:custom", "distance", 0.85);
            assertTrue(r.getIsDetected());
        }
    }

    @Test
    void canrangeDuplicateNameThrows() {
        try (ScrimmageCANrange r = new ScrimmageCANrange("dup")) {
            assertThrows(IllegalArgumentException.class, () -> new ScrimmageCANrange("dup"));
        }
        new ScrimmageCANrange("dup").close();
    }

    @Test
    void canrangeReadsZeroWithoutScrimmage() {
        SimDevice held = hal ? SimDevice.create("ScrimmageCANrange:held") : null;
        try (ScrimmageCANrange r = new ScrimmageCANrange("held")) {
            assertEquals(0.0, r.getDistanceMeters(), 0);
            assertTrue(r.getIsDetected());
        } finally {
            if (held != null) {
                held.close();
            }
        }
    }

    @Test
    void disabledRobotGivesNeutral() {
        requireHal();
        try (ScrimmageKraken k = new ScrimmageKraken("disabled", new KrakenSettings())) {
            k.setControl(ControlRequest.voltage(6));
            setEnabled(false);
            step();
            assertEquals(0, out("ScrimmageMotor:disabled", "motorVoltage"), EPS);
            setEnabled(true);
            step();
            assertEquals(6, out("ScrimmageMotor:disabled", "motorVoltage"), EPS);
        }
    }

    @Test
    void requestAtTheTickTimeWaitsForTheNextTick() {
        requireHal();
        try (ScrimmageKraken k = new ScrimmageKraken("same_time", new KrakenSettings())) {
            now += 5000;
            k.setControl(ControlRequest.voltage(5)); // robot code at the tick's own time
            ScrimmageKraken.updateAll(now, ScrimmageKraken.PERIOD);
            assertEquals(0, out("ScrimmageMotor:same_time", "motorVoltage"), EPS);
            now += 5000;
            ScrimmageKraken.updateAll(now, ScrimmageKraken.PERIOD);
            assertEquals(5, out("ScrimmageMotor:same_time", "motorVoltage"), EPS);
        }
    }

    @Test
    void tickUsesTheNewestRequestBeforeIt() {
        requireHal();
        try (ScrimmageKraken k = new ScrimmageKraken("newest", new KrakenSettings())) {
            k.setControl(ControlRequest.voltage(3));
            now += 1;
            k.setControl(ControlRequest.voltage(4));
            now += 4999;
            k.setControl(ControlRequest.voltage(9)); // at the tick's time: next tick
            ScrimmageKraken.updateAll(now, ScrimmageKraken.PERIOD);
            assertEquals(4, out("ScrimmageMotor:newest", "motorVoltage"), EPS);
            now += 5000;
            ScrimmageKraken.updateAll(now, ScrimmageKraken.PERIOD);
            assertEquals(9, out("ScrimmageMotor:newest", "motorVoltage"), EPS);
        }
    }

    @Test
    void followAtTheTickTimeWaitsForTheNextTick() {
        requireHal();
        try (ScrimmageKraken leader = new ScrimmageKraken("f_leader", new KrakenSettings());
                ScrimmageKraken follower = new ScrimmageKraken("f_follower", new KrakenSettings())) {
            leader.setControl(ControlRequest.voltage(6));
            follower.setControl(ControlRequest.voltage(1));
            now += 5000;
            follower.follow(leader, true);
            ScrimmageKraken.updateAll(now, ScrimmageKraken.PERIOD);
            assertEquals(1, out("ScrimmageMotor:f_follower", "motorVoltage"), EPS);
            now += 5000;
            ScrimmageKraken.updateAll(now, ScrimmageKraken.PERIOD);
            assertEquals(-6, out("ScrimmageMotor:f_follower", "motorVoltage"), EPS);
        }
    }

    @Test
    void readingAtTheTickTimeShowsThePreviousTick() {
        requireHal();
        try (ScrimmageKraken k = new ScrimmageKraken("readback", new KrakenSettings())) {
            k.setControl(ControlRequest.voltage(2));
            step();
            assertEquals(2, k.getMotorVoltage(), EPS);
            k.setControl(ControlRequest.voltage(8));
            now += 4999; // the next tick's time
            ScrimmageKraken.updateAll(now, ScrimmageKraken.PERIOD);
            assertEquals(8, out("ScrimmageMotor:readback", "motorVoltage"), EPS); // scrimmage sees it now
            // Robot code at the tick's own time sees the tick before, whichever thread ran first.
            assertEquals(2, k.getMotorVoltage(), EPS);
            assertEquals(KrakenModel.statorCurrent(2, 0), k.getStatorCurrent(), EPS);
            now += 1;
            assertEquals(8, k.getMotorVoltage(), EPS);
            assertEquals(KrakenModel.statorCurrent(8, 0), k.getStatorCurrent(), EPS);
        }
    }

    @Test
    void readingWalksLeadersWithoutHoldingItsOwnLock() throws Exception {
        requireHal();
        try (ScrimmageKraken a = new ScrimmageKraken("swap_a", new KrakenSettings());
                ScrimmageKraken b = new ScrimmageKraken("swap_b", new KrakenSettings())) {
            b.follow(a, true);
            step(); // the control loop now has b following a
            b.setControl(ControlRequest.voltage(1)); // swap roles: until the next tick the robot thread has a
            a.follow(b, true); // following b while the control loop still has b following a
            // The control loop, updating b, holds b's lock and walks to a. A reading of a on the robot thread
            // walks to b: it must not hold a's lock meanwhile, or the two threads would deadlock.
            Thread reader;
            synchronized (b) {
                reader = new Thread(a::getPosition, "reader");
                reader.start();
                ThreadMXBean mx = ManagementFactory.getThreadMXBean();
                long deadline = System.nanoTime() + 5_000_000_000L;
                while (reader.getState() != Thread.State.BLOCKED && reader.isAlive()) {
                    assertTrue(System.nanoTime() < deadline, "the reader never waited for b's lock");
                    Thread.onSpinWait();
                }
                ThreadInfo info = mx.getThreadInfo(new long[] {reader.getId()}, true, false)[0];
                for (MonitorInfo m : info.getLockedMonitors()) {
                    assertFalse(m.getIdentityHashCode() == System.identityHashCode(a),
                            "the reader holds a's lock while waiting for b's");
                }
            }
            reader.join(5000);
            assertFalse(reader.isAlive());
        }
    }

    @Test
    void commandsAfterCloseAreIgnored() {
        requireHal();
        ScrimmageKraken k = new ScrimmageKraken("closed", new KrakenSettings());
        k.close();
        for (int i = 0; i < 3; i++) {
            k.setControl(ControlRequest.voltage(i)); // must not pile up for a control loop that's gone
            now += 1;
        }
        assertEquals(0, k.pendingCount());
    }

    @Test
    void sharedNotifierRunsOnTheSimulatedClock() {
        requireHal();
        ScrimmageKraken.clock = ScrimmageKraken.HAL_CLOCK;
        try (ScrimmageKraken k = new ScrimmageKraken("clocked", new KrakenSettings())) {
            k.setControl(ControlRequest.voltage(7));
            assertEquals(0, out("ScrimmageMotor:clocked", "motorVoltage"), EPS);
            SimHooks.stepTiming(0.02); // waits for the notifiers that come due to run
            assertEquals(7, out("ScrimmageMotor:clocked", "motorVoltage"), EPS);
        }
    }
}
