package scrimmage.sim;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class TalonControlTest {
    private static final double VBUS = 12;
    private static final double DT = 0.005;
    private static final double EPS = 1e-9;

    /** One update with the mechanism and rotor at rest. */
    private static double run(KrakenSettings s, ControlRequest r, double position) {
        return new TalonControl(s).update(r, position, 0, 0, VBUS, DT);
    }

    @Test
    void voltageRequestPassesThroughWithinPeaks() {
        assertEquals(5, run(new KrakenSettings(), ControlRequest.voltage(5), 0), EPS);
        // With a supply above the default ±16 V peaks, the peaks clamp.
        assertEquals(16, new TalonControl(new KrakenSettings()).update(ControlRequest.voltage(20), 0, 0, 0, 24, DT),
                EPS);
        assertEquals(-16, new TalonControl(new KrakenSettings()).update(ControlRequest.voltage(-20), 0, 0, 0, 24, DT),
                EPS);
        KrakenSettings s = new KrakenSettings().withPeakForwardVoltage(10);
        assertEquals(10, run(s, ControlRequest.voltage(12), 0), EPS);
    }

    @Test
    void voltageRequestIsCappedAtSupplyVoltage() {
        assertEquals(12, run(new KrakenSettings(), ControlRequest.voltage(14), 0), EPS);
        assertEquals(-12, run(new KrakenSettings(), ControlRequest.voltage(-14), 0), EPS);
        TalonControl c = new TalonControl(new KrakenSettings());
        assertEquals(10.5, c.update(ControlRequest.voltage(12), 0, 0, 0, 10.5, DT), EPS);
        assertEquals(0, new TalonControl(new KrakenSettings()).update(ControlRequest.voltage(6), 0, 0, 0, 0, DT), EPS);
    }

    @Test
    void closedLoopOutputIsCappedAtSupplyVoltage() {
        KrakenSettings s = new KrakenSettings().withKP(100);
        assertEquals(12, run(s, ControlRequest.position(1.0), 0), EPS);
        assertEquals(-12, run(s, ControlRequest.position(-1.0), 0), EPS);
    }

    @Test
    void currentsFollowTheCappedVoltage() {
        TalonControl c = new TalonControl(new KrakenSettings());
        double v = c.update(ControlRequest.voltage(15), 0, 0, 30, VBUS, DT);
        assertEquals(12, v, EPS);
        assertEquals(KrakenModel.statorCurrent(12, 30), c.statorCurrent(), EPS);
        assertEquals(KrakenModel.statorCurrent(12, 30), c.supplyCurrent(), EPS); // |V| = vbus
    }

    @Test
    void fullDutyCycleGivesSupplyVoltage() {
        TalonControl c = new TalonControl(new KrakenSettings());
        assertEquals(11, c.update(ControlRequest.dutyCycle(1.0), 0, 0, 0, 11, DT), EPS);
        assertEquals(-11, c.update(ControlRequest.dutyCycle(-1.0), 0, 0, 0, 11, DT), EPS);
    }

    @Test
    void dutyCycleUsesBusVoltage() {
        TalonControl c = new TalonControl(new KrakenSettings());
        assertEquals(5.5, c.update(ControlRequest.dutyCycle(0.5), 0, 0, 0, 11, DT), EPS);
    }

    @Test
    void dutyCycleRequestIsClampedByDutyPeaks() {
        KrakenSettings s = new KrakenSettings().withPeakForwardDutyCycle(0.5);
        assertEquals(6, run(s, ControlRequest.dutyCycle(0.8), 0), EPS);
    }

    @Test
    void positionPOnly() {
        KrakenSettings s = new KrakenSettings().withKP(2);
        assertEquals(1.5, run(s, ControlRequest.position(1.0), 0.25), EPS);
    }

    @Test
    void positionFeedforwardIsAdded() {
        KrakenSettings s = new KrakenSettings().withKP(2);
        assertEquals(2.0, run(s, ControlRequest.position(1.0).withFeedforward(0.5), 0.25), EPS);
    }

    @Test
    void velocityFeedforwardAndKsVelocitySign() {
        KrakenSettings s = new KrakenSettings().withKV(0.12).withKS(0.25);
        assertEquals(1.45, run(s, ControlRequest.velocity(10), 0), EPS);
        assertEquals(-1.45, run(s, ControlRequest.velocity(-10), 0), EPS);
    }

    @Test
    void velocityErrorUsesMeasuredVelocity() {
        KrakenSettings s = new KrakenSettings().withKP(0.5);
        TalonControl c = new TalonControl(s);
        assertEquals(1.0, c.update(ControlRequest.velocity(10), 0, 8, 8, VBUS, DT), EPS);
    }

    @Test
    void ksClosedLoopSignFollowsPositionError() {
        KrakenSettings s = new KrakenSettings()
                .withKS(0.3)
                .withStaticFeedforwardSign(KrakenSettings.StaticFeedforwardSign.CLOSED_LOOP_SIGN);
        assertEquals(-0.3, run(s, ControlRequest.position(1.0), 2.0), EPS);
        assertEquals(0.3, run(s, ControlRequest.position(3.0), 2.0), EPS);
    }

    @Test
    void ksVelocitySignIsZeroForPlainPositionHold() {
        KrakenSettings s = new KrakenSettings().withKS(0.3);
        assertEquals(0, run(s, ControlRequest.position(1.0), 2.0), EPS);
    }

    @Test
    void elevatorStaticKgAlwaysAdded() {
        KrakenSettings s = new KrakenSettings().withKG(0.5);
        assertEquals(0.5, run(s, ControlRequest.position(0.25), 0.25), EPS);
        assertEquals(0.5, run(s, ControlRequest.position(0), 0), EPS);
        assertEquals(0.5, run(s, ControlRequest.velocity(0), 0.4), EPS);
    }

    @Test
    void armCosineKgScalesWithAngle() {
        KrakenSettings s = new KrakenSettings()
                .withKG(0.5)
                .withGravityType(KrakenSettings.GravityType.ARM_COSINE);
        assertEquals(0, run(s, ControlRequest.position(0.25), 0.25), EPS);
        assertEquals(0.5, run(s, ControlRequest.position(0), 0), EPS);
        assertEquals(-0.5, run(s, ControlRequest.position(0.5), 0.5), EPS);
    }

    @Test
    void armPositionOffsetShiftsTheCosine() {
        // An L-shaped arm whose center of gravity is a quarter turn ahead of its zero: kG peaks at -0.25 rotations.
        KrakenSettings s = new KrakenSettings()
                .withKG(0.5)
                .withGravityType(KrakenSettings.GravityType.ARM_COSINE)
                .withGravityArmPositionOffset(0.25);
        assertEquals(0.5, run(s, ControlRequest.position(-0.25), -0.25), EPS);
        assertEquals(0, run(s, ControlRequest.position(0), 0), EPS);
        assertEquals(0.5 * Math.cos(2 * Math.PI * 0.35), run(
                new KrakenSettings().withKG(0.5).withGravityType(KrakenSettings.GravityType.ARM_COSINE)
                        .withGravityArmPositionOffset(0.1), ControlRequest.position(0.25), 0.25), EPS);
    }

    @Test
    void armPositionOffsetIsHeldToItsDocumentedRange() {
        KrakenSettings s = new KrakenSettings().withKG(0.5).withGravityType(KrakenSettings.GravityType.ARM_COSINE);
        assertEquals(0, run(s.copy().withGravityArmPositionOffset(0.4), ControlRequest.position(0), 0), EPS);   // 0.25
        assertEquals(0, run(s.copy().withGravityArmPositionOffset(-0.4), ControlRequest.position(0), 0), EPS);  // -0.25
    }

    @Test
    void armPositionOffsetDoesNothingForAnElevator() {
        KrakenSettings s = new KrakenSettings().withKG(0.5).withGravityArmPositionOffset(0.2);
        assertEquals(0.5, run(s, ControlRequest.position(0), 0), EPS);
    }

    @Test
    void integralAccumulatesErrorTimesDt() {
        KrakenSettings s = new KrakenSettings().withKI(1);
        TalonControl c = new TalonControl(s);
        double out = 0;
        for (int i = 0; i < 100; i++) {
            out = c.update(ControlRequest.position(1.1), 1.0, 0, 0, VBUS, DT);
        }
        assertEquals(0.05, out, EPS);
    }

    @Test
    void resetClearsIntegrator() {
        TalonControl c = new TalonControl(new KrakenSettings().withKI(1));
        for (int i = 0; i < 100; i++) {
            c.update(ControlRequest.position(1.1), 1.0, 0, 0, VBUS, DT);
        }
        c.reset();
        assertEquals(0.0005, c.update(ControlRequest.position(1.1), 1.0, 0, 0, VBUS, DT), EPS);
    }

    @Test
    void derivativeUsesErrorChangeOverDt() {
        TalonControl c = new TalonControl(new KrakenSettings().withKD(0.01));
        assertEquals(0, c.update(ControlRequest.position(1.0), 0, 0, 0, VBUS, DT), EPS);
        // Error falls from 1.0 to 0.9 in one step: de/dt = -20 rot/s.
        assertEquals(-0.2, c.update(ControlRequest.position(1.0), 0.1, 0, 0, VBUS, DT), EPS);
    }

    @Test
    void continuousWrapTakesShortestWay() {
        KrakenSettings s = new KrakenSettings().withKP(1).withContinuousWrap(true);
        assertEquals(-0.3, run(s, ControlRequest.position(0.8), 2.1), EPS);
        KrakenSettings noWrap = new KrakenSettings().withKP(1);
        assertEquals(-1.3, run(noWrap, ControlRequest.position(0.8), 2.1), EPS);
    }

    @Test
    void forwardSoftLimitBlocksForwardOutput() {
        KrakenSettings s = new KrakenSettings()
                .withForwardSoftLimitThreshold(1.0)
                .withForwardSoftLimitEnable(true);
        assertEquals(0, run(s, ControlRequest.voltage(3), 1.05), EPS);
        assertEquals(-3, run(s, ControlRequest.voltage(-3), 1.05), EPS);
        assertEquals(3, run(s, ControlRequest.voltage(3), 0.5), EPS);
    }

    @Test
    void reverseSoftLimitBlocksReverseOutput() {
        KrakenSettings s = new KrakenSettings()
                .withReverseSoftLimitThreshold(-1.0)
                .withReverseSoftLimitEnable(true);
        assertEquals(0, run(s, ControlRequest.voltage(-3), -1.05), EPS);
        assertEquals(3, run(s, ControlRequest.voltage(3), -1.05), EPS);
    }

    @Test
    void statorLimitCapsCurrent() {
        KrakenSettings s = new KrakenSettings()
                .withStatorCurrentLimit(40)
                .withStatorCurrentLimitEnable(true);
        TalonControl c = new TalonControl(s);
        double v = c.update(ControlRequest.voltage(12), 0, 0, 0, VBUS, DT);
        assertTrue(v > 0, "still drives forward");
        assertTrue(KrakenModel.statorCurrent(v, 0) <= 40 + 0.5, "stator current " + KrakenModel.statorCurrent(v, 0));
        assertEquals(KrakenModel.statorCurrent(v, 0), c.statorCurrent(), EPS);
    }

    @Test
    void supplyLimitCapsSupplyCurrent() {
        KrakenSettings s = new KrakenSettings()
                .withSupplyCurrentLimit(30)
                .withSupplyCurrentLimitEnable(true);
        TalonControl c = new TalonControl(s);
        double v = c.update(ControlRequest.voltage(12), 0, 0, 20, VBUS, DT);
        assertTrue(v > 0, "still drives forward");
        assertTrue(c.supplyCurrent() <= 30 + 0.5, "supply current " + c.supplyCurrent());
        assertEquals(c.statorCurrent() * Math.abs(v) / VBUS, c.supplyCurrent(), EPS);
    }

    @Test
    void currentsReportedWithoutLimits() {
        TalonControl c = new TalonControl(new KrakenSettings());
        c.update(ControlRequest.voltage(6), 0, 0, 30, VBUS, DT);
        assertEquals(KrakenModel.statorCurrent(6, 30), c.statorCurrent(), EPS);
        assertEquals(KrakenModel.statorCurrent(6, 30) * 6 / VBUS, c.supplyCurrent(), EPS);
    }

    @Test
    void dutyCycleOutputTypeGainsAreInDutyUnits() {
        KrakenSettings s = new KrakenSettings()
                .withKP(0.1)
                .withOutputType(KrakenSettings.OutputType.DUTY_CYCLE);
        TalonControl c = new TalonControl(s);
        assertEquals(1.0, c.update(ControlRequest.position(1.0), 0, 0, 0, 10, DT), EPS);
    }

    @Test
    void motionMagicFollowsProfileTowardTarget() {
        KrakenSettings s = new KrakenSettings()
                .withKV(0.1)
                .withMotionMagicCruiseVelocity(2)
                .withMotionMagicAcceleration(4);
        TalonControl c = new TalonControl(s);
        // First step from rest: the profile accelerates at 4 rps/s, so vRef = 0.02 rps.
        assertEquals(0.002, c.update(ControlRequest.motionMagic(10), 0, 0, 0, VBUS, DT), EPS);
        assertEquals(0.004, c.update(ControlRequest.motionMagic(10), 0, 0, 0, VBUS, DT), EPS);
    }

    @Test
    void motionMagicVelocityRampsReference() {
        KrakenSettings s = new KrakenSettings().withKV(0.1).withMotionMagicAcceleration(10);
        TalonControl c = new TalonControl(s);
        assertEquals(0.005, c.update(ControlRequest.motionMagicVelocity(5), 0, 0, 0, VBUS, DT), EPS);
    }

    @Test
    void neutralGivesZero() {
        KrakenSettings s = new KrakenSettings().withKG(0.5).withKP(3);
        TalonControl c = new TalonControl(s);
        assertEquals(0, c.update(ControlRequest.neutral(), 0.3, 1, 1, VBUS, DT), EPS);
    }
}
