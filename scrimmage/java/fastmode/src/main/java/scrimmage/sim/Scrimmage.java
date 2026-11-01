package scrimmage.sim;

import edu.wpi.first.hal.SimDevice;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

/**
 * The fast-mode check, plus what the simulated devices share: their names and their HAL SimDevices.
 *
 * <p>scrimmage tells the robot program it runs in fast mode with {@code SCRIMMAGE_MODE=fast}. Robot code uses
 * {@link #fastMode()} to choose its scrimmage IO classes; on a RoboRIO the variable is never set.
 */
public final class Scrimmage {
    private static final Set<String> NAMES = new HashSet<>();

    private Scrimmage() {}

    /** True when scrimmage runs this robot program in fast mode ({@code SCRIMMAGE_MODE=fast}). */
    public static boolean fastMode() {
        return fastMode(System.getenv());
    }

    static boolean fastMode(Map<String, String> env) {
        return "fast".equals(env.get("SCRIMMAGE_MODE"));
    }

    /** Reserves a device name ("ScrimmageMotor:elevator"), or throws if a device already uses it. */
    static synchronized void claim(String type, String name) {
        if (!NAMES.add(type + ":" + name)) {
            throw new IllegalArgumentException("a " + type + " device named \"" + name + "\" already exists");
        }
    }

    static synchronized void release(String type, String name) {
        NAMES.remove(type + ":" + name);
    }

    /**
     * The HAL SimDevice {@code type:name}, or null where there is none: on a RoboRIO (the HAL returns null), when
     * the HAL already holds that name, or when the HAL's natives aren't loaded at all (a plain unit test).
     */
    static SimDevice createSimDevice(String type, String name) {
        try {
            return SimDevice.create(type + ":" + name);
        } catch (LinkageError e) {
            return null;
        }
    }
}
