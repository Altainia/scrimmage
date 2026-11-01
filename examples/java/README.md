# Using Scrimmage with a Java (or C++) WPILib project

Java robots, including ones that use CTRE Phoenix 6, run under Scrimmage through a Gradle init script
that changes nothing in your repo. See [Java robots (Phoenix 6)](../../README.md#java-robots-phoenix-6)
in the top-level README, and `examples/java_swerve` for a working project.

The old Java shim (`shims/java/WsCanShim.java`) is no longer needed for Phoenix 6.

## Without the init script

For a robot that uses only WPILib devices (PWM motors, DIO encoders and so on), two `build.gradle` lines
load WPILib's HALSim WebSocket client, which connects to Scrimmage instead of real hardware:

```groovy
// Connect desktop simulation to scrimmage (ws://localhost:3300/wpilibws)
wpi.sim.envVar("HALSIMWS_HOST", "localhost")
wpi.sim.envVar("HALSIMWS_PORT", "3300")
wpi.sim.addWebsocketsClient().defaultEnabled = true
```

Then `scrimmage serve configs/your_robot.toml --viewer`, and run your code with **WPILib: Simulate Robot
Code** or `./gradlew simulateJava`. This path is untested here; the init script path above is the
tested one.
