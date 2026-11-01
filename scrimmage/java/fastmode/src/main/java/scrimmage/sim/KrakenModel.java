package scrimmage.sim;

import edu.wpi.first.math.system.plant.DCMotor;

/**
 * The Kraken X60's electrical model, from WPILib's published motor constants ({@code DCMotor.getKrakenX60(1)}):
 * stator current = (V - back-EMF) / R.
 */
public final class KrakenModel {
    private static final DCMotor MOTOR = DCMotor.getKrakenX60(1);

    private KrakenModel() {}

    /** Stator current (A) with {@code volts} across the motor turning at {@code motorRps} (rotor rps). */
    public static double statorCurrent(double volts, double motorRps) {
        return (volts - backEmf(motorRps)) / MOTOR.rOhms;
    }

    /** The voltage across the motor that gives a stator current of {@code amps} at {@code motorRps}. */
    public static double voltsForStatorCurrent(double amps, double motorRps) {
        return amps * MOTOR.rOhms + backEmf(motorRps);
    }

    /** Winding resistance (ohms). */
    static double resistance() {
        return MOTOR.rOhms;
    }

    /** Back-EMF (V) at {@code motorRps}. */
    static double backEmf(double motorRps) {
        return 2 * Math.PI * motorRps / MOTOR.KvRadPerSecPerVolt;
    }
}
