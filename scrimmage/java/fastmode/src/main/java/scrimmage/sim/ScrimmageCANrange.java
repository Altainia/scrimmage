package scrimmage.sim;

import edu.wpi.first.hal.SimDevice;
import edu.wpi.first.hal.SimDevice.Direction;
import edu.wpi.first.hal.SimDouble;

/**
 * A simulated distance sensor (a CTRE CANrange), the HAL SimDevice {@code ScrimmageCANrange:<name>}. scrimmage
 * writes the distance (meters).
 *
 * <p>Where scrimmage isn't running (a RoboRIO, a plain test), the distance reads 0, so the sensor reads "detected".
 */
public class ScrimmageCANrange implements AutoCloseable {
    static final String TYPE = "ScrimmageCANrange";
    /** Phoenix 6's default ProximityParams (phoenix6 26.3.0): the threshold and the hysteresis, meters. */
    static final double DEFAULT_THRESHOLD = 0.4;
    static final double DEFAULT_HYSTERESIS = 0.01;

    private final String name;
    private final SimDevice device;
    private final SimDouble distance;
    private double threshold = DEFAULT_THRESHOLD;
    private double hysteresis = DEFAULT_HYSTERESIS;
    private boolean detected = true;    // as Phoenix's: distance 0 at boot

    public ScrimmageCANrange(String name) {
        Scrimmage.claim(TYPE, name);
        this.name = name;
        device = Scrimmage.createSimDevice(TYPE, name);
        distance = device != null ? device.createDouble("distance", Direction.kInput, 0) : null;
    }

    /** The distance to what the sensor sees, meters, in whole millimeters (as Phoenix keeps it). */
    public double getDistanceMeters() {
        double d = distance == null ? 0 : distance.get();
        return Math.floor(d * 1000 + 1e-6) / 1000;
    }

    /**
     * Whether something is closer than the proximity threshold. It latches with the hysteresis, as Phoenix's does:
     * it starts true, turns false when the distance goes above threshold + hysteresis, and turns true when it goes
     * below threshold - hysteresis. It looks at the distance when called, so call it every loop.
     */
    public synchronized boolean getIsDetected() {
        double d = getDistanceMeters();
        if (d > threshold + hysteresis) {
            detected = false;
        } else if (d < threshold - hysteresis) {
            detected = true;
        }
        return detected;
    }

    /** Phoenix's {@code ProximityParams.ProximityThreshold}, meters (default 0.4). */
    public synchronized void setProximityThreshold(double meters) {
        threshold = meters;
    }

    /** Phoenix's {@code ProximityParams.ProximityHysteresis}, meters (default 0.01). */
    public synchronized void setProximityHysteresis(double meters) {
        hysteresis = meters;
    }

    @Override
    public void close() {
        if (device != null) {
            device.close();
        }
        Scrimmage.release(TYPE, name);
    }
}
