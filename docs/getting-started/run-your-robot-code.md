# Run your robot code in the sim

For programmers who want their team's real robot code (Java or Python) to drive a simulated robot, with motors,
wheels, friction and a sagging battery, before the real robot exists.

[← All guides](README.md) · Needs: [the install](install.md), the robot-code line of step 3 (and, for Java,
WPILib and `scrimmage java build`)

Your robot code doesn't change. Scrimmage pretends to be the robot's hardware: it reads your motor commands and
sends back encoder counts, gyro angles and battery voltage from the physics.

## 1. Run an example first

Before your own code, make sure an example runs on your computer. In the `scrimmage` folder, with the venv active:

| Your team uses | Run | Takes about |
|---|---|---|
| Java with CTRE (Kraken, Talon FX, CANcoder, Pigeon 2) | `scrimmage run scenarios/swerve_auto.toml` | 20 s (the first time, a few minutes of Gradle downloads) |
| Java with REV (SPARK MAX or Flex) | `scrimmage run scenarios/maxswerve_auto.toml` | 10 s (plus Gradle downloads the first time) |
| Python (RobotPy) | `scrimmage run scenarios/kitbot_auto.toml` | a few seconds |

Each runs the example robot's autonomous routine with no window and ends with a verdict:

```
PASS  swerve_auto.toml
  note: Phoenix 6 simulation runs on the wall clock, so this robot runs in real time (not lockstep); ...
report: runs/swerve_auto-20261031-114318/report.md
```

Open the `report.md` it names: the checks, and a picture of the path the robot drove from above.

## 2. Drive it yourself

```bash
scrimmage edit configs/swerve.toml        # or configs/maxswerve.toml, configs/kitbot.toml
```

A page opens in your web browser. Click the **Drive** tab, then **Start**. Pick **Teleop**, press **Enter** to enable,
and drive with the keyboard (WASD and IJKL act as an Xbox controller's two sticks) or a plugged-in gamepad. **Space**
disables. You can drag the robot around, pause, and record a drive. [docs/drive.md](../drive.md) has every control.

## 3. Your own robot

You need a robot config: a text file that says what the robot is made of (a chassis, swerve modules, an arm...) and
which CAN ID each part has. Where it lives depends on your team:

- **Your team uses team folders** (the usual way when CAD and programming share files): robot files live in the
  team's **CAD folder**, and robot projects in its **code folder**. On each computer, set both once: run
  `scrimmage edit`, and on the start page find **This computer's team folders** (see
  [the install, step 3](install.md#3-install-the-parts-you-need)). Then make or open the robot file in the CAD
  folder, and point it at your project in the code folder (below): the file says "the code folder" instead of this
  laptop's path, so it works on every computer.
- **On your own**: keep it in a `sim` folder inside your robot project, so it's saved with your code (the steps below
  do this).

**Make one from a template.** In a terminal in your robot project's folder (with Scrimmage's venv active: run
`source <path to scrimmage>/.venv/bin/activate` first):

```bash
mkdir sim
scrimmage edit sim/robot.toml --new swerve           # CTRE swerve (SDS MK4i, Krakens)
```

For other robots, use `--new swerve-rev` (REV MAXSwerve) or `--new differential` (tank drive). The page that
opens shows the robot in 3D. Press Ctrl+C in the terminal when you're done with it.

**Point it at your code.** On the page, click **Robot code** above the 3D view (the switch beside **3D view**).
It has three numbered parts (a picture is in [the assembler guide](../assembler.md#the-robot-code-panel)):

1. **Your robot code.** Click **Browse…** (it reads **Change…** once a folder is set), click through to your robot project's folder (the one with `build.gradle`
   for Java, or `robot.py` for Python) and press **Use this folder**. It answers in plain words, such as
   `Java (GradleRIO) · Phoenix 6`. A folder that isn't a robot project is refused and nothing is written.
2. **Ready to start?** A checklist. A tick means ready, a warning means something is missing, and an "i" is good to know.
   A missing item has a button that fixes it: **Build it** (Scrimmage's Java helper), **Build now** (your project,
   the first time takes a few minutes) or **Install it** (the lockstep add-on). The CTRE real-time note is the same
   one as in "Things that surprise people" below.
3. **Where it starts.** There is no default, so you choose: press **Pick on the field**, choose the **2026 field** or
   the **Plain field**, then click where the robot starts and hold the button down and drag to turn it. A 2026 field
   also has named starts to click, and a **No balls on the floor** switch (the field is quicker without 360 balls).
   **Exact position** has the numbers. **Cancel** takes back what you changed in the picker.

Then press **Start and check devices** (or **Drive**). Both ask for a start first if you haven't picked one. A Java
project that hasn't been built on this computer yet (or whose last build failed) doesn't start: the panel says so,
with **Build now** beside it (a scenario's **Run** says the same, with **Go to Robot code**). While the robot runs, **Stop the robot** stops it (it has to stop before you open
another file or run a scenario).

**Do the devices match?** After a few seconds a table appears: one row per motor, encoder or gyro in the model, with
**Model part**, **Model expects**, **Your code made** and a status: matches, nothing with this ID, not in the model, or
another type. **Not checked yet** means Scrimmage doesn't compare that kind of device yet: PWM motors, DIO and
analog encoders and sensors (the kitbot's motors are PWM, for example). Only Phoenix 6 (Talon FX, CANcoder, Pigeon 2)
and REV SPARK devices are matched, so check the others' ports in your code by hand. Wrong CAN IDs are the first thing
a team gets wrong, so look here. On a row that has no match, a
**Use TalonFX 13**-style button picks the device your code made instead. Press **Apply the matches and restart** (it reads **Apply the matches** while the robot isn't running) to
write them all to the robot file (one **Undo** takes them back). The **Drive** tab also says how many devices don't
match.

**Brake or coast, and invert.** Scrimmage can't read those from your code. Select a part: each motor in the
inspector has **When stopped** (Default, Brake or Coast) and **Invert (turn the other way)**. Set them the way your
code does.

**Battery.** At the foot of the Robot code panel, **More settings** has the battery's voltage and resistance.

**Without the page.** The page writes a `[robot_program]` section in the robot file. If you'd rather edit it by hand,
open `sim/robot.toml` in a text editor and find the commented-out `[robot_program]` lines. Replace them with the one
for your language:

```toml
# Java (GradleRIO): the robot project is the folder above sim/
[robot_program]
command = ["./gradlew", "-I", "{scrimmage_java}/scrimmage.init.gradle", "simulateJava"]
cwd = ".."
```

```toml
# Python (RobotPy)
[robot_program]
command = ["{python}", "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]
cwd = ".."
```

Type the `{scrimmage_java}` and `{python}` exactly as shown: Scrimmage fills them in. By hand, the start is
`start_pose = [x, y, heading]` under `[robot]`.

**Match your CAN IDs and parts.** Each `[[part]]` has an `ids = { ... }` line. Change the numbers to your robot's
(for a CTRE swerve made with Tuner X, they're in `TunerConstants.java`), and `ratio` to your modules' gear ratio. If
your robot has an arm, an elevator or a shooter, add them in the page (`scrimmage edit sim/robot.toml`, then **Add**
in the parts library). `scrimmage parts` lists every part you can use. Brake mode and invert are set per motor in the inspector (see above), or by hand with
`neutral = "brake"` and `invert = true`.

**Drive it.** Press **Drive** on the Robot code panel, or open the **Drive** tab as in step 2.

To check your robot automatically every time the code changes, go on to
[Test your robot code](test-your-robot-code.md).

## Things that surprise people

- **"Phoenix 6 simulation runs on the wall clock, so this robot runs in real time."** Normal for CTRE robots. CTRE's
  simulation only runs in real time, so a 15 s auto takes 15 s. REV and Python robots run faster than real time. If
  your CTRE team wants faster, exactly repeatable runs (for example in CI), read [fast mode](../fast-mode.md).
- **"As expected, the scrimmage agent ignored the robot code's own simulation (1187 sim writes:
  SwerveDrivetrain.updateSimState ...)".** Also normal. Code made by Tuner X has its own small simulation that would fight Scrimmage's physics, so Scrimmage
  turns it off while the robot runs. Nothing in your files changes.
- **The robot drives but odometry is off by a few centimeters.** That's real: wheels slip and scrub. Scrimmage
  shows the difference between where the robot thinks it is and where it really is.

## If something goes wrong

- **"the robot program didn't connect within 180 s"** on a Java robot: the first Gradle build can take minutes.
  Press **Build now** in the Robot code panel (or run `./gradlew build` once in your robot project), then try again.
- **A motor doesn't move, or a check fails for no clear reason**: look in the terminal output for a `WARNING` line
  like `config expects Talon FX (v6)[21], but the robot program hasn't created it`. A CAN ID or device type in the
  config doesn't match your code; the same line lists the devices your code did create. The device table on the Robot code panel
  shows the same thing, and a scenario's report lists it as a note.
- **"port 3300 is in use"** or **"NetworkTables port 5810 is already in use"**: another simulation is still running. Only one can run at a
  time on a computer. Close the other one (the browser's Drive tab, an agent session or a terminal).
- **The robot program crashed**: the error is in the run's `robot.log`, or shown in the Drive tab. Fix the code and
  run again.
- **Windows or macOS**: runs are always real time, and untested so far.
- More in the README: [Java robots](../../README.md#java-robots-phoenix-6),
  [REV robots](../rev.md) and [troubleshooting](../../README.md#sessions-environment-and-troubleshooting).
