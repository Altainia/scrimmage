// Aiming table from scrimmage's launcher model. Each row is
// {distance_m, wheel_rps, hood_deg, flight_s, apex_m}: robot center to goal center, the wheel speed and hood
// angle whose ball crosses the goal's rim height descending at its center, the time from exit to that
// crossing, and the ball's highest point.
// Made by: scrimmage launcher aim CONFIG --goal hub_blue --distances 1.5:6.0:0.25 --java FILE --java-class frc.robot.subsystems.shooter.AimTable
// UNCALIBRATED: estimates from preset material numbers, not fitted to measurements.
package frc.robot.subsystems.shooter;

public final class AimTable {
    private AimTable() {}

    public static final double[][] ROWS = {
        {1.5000, 52.0657, 78.0000, 1.2956, 3.3064},
        {1.7500, 51.3138, 76.0000, 1.2626, 3.2047},
        {2.0000, 50.9311, 74.0000, 1.2380, 3.1302},
        {2.2500, 50.7498, 72.0000, 1.2189, 3.0736},
        {2.5000, 50.7297, 70.0000, 1.2017, 3.0231},
        {2.7500, 50.8505, 68.0000, 1.1871, 2.9803},
        {3.0000, 51.0989, 66.0000, 1.1737, 2.9417},
        {3.2500, 51.4279, 64.0000, 1.1613, 2.9057},
        {3.5000, 51.8643, 62.0000, 1.1493, 2.8714},
        {3.7500, 52.3746, 60.0000, 1.1375, 2.8378},
        {4.0000, 53.9188, 60.0000, 1.1836, 2.9608},
        {4.2500, 54.4357, 58.0000, 1.1690, 2.9180},
        {4.5000, 55.8926, 58.0000, 1.2110, 3.0327},
        {4.7500, 57.3160, 58.0000, 1.2524, 3.1502},
        {5.0000, 57.7994, 56.0000, 1.2329, 3.0896},
        {5.2500, 58.4171, 54.0000, 1.2142, 3.0321},
        {5.5000, 59.1824, 52.0000, 1.1969, 2.9791},
        {5.7500, 59.8807, 48.0000, 1.1280, 2.7878}
        // 6 m: unreachable, left out
    };
}
