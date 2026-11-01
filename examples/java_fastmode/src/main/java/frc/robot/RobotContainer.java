// Copyright (c) 2021-2026 Littleton Robotics
// http://github.com/Mechanical-Advantage
//
// Use of this source code is governed by a BSD
// license that can be found in the LICENSE file
// at the root directory of this project.

package frc.robot;

import com.pathplanner.lib.auto.AutoBuilder;
import com.pathplanner.lib.auto.NamedCommands;
import edu.wpi.first.math.geometry.Pose2d;
import edu.wpi.first.math.geometry.Rotation2d;
import edu.wpi.first.wpilibj.DriverStation;
import edu.wpi.first.wpilibj.GenericHID;
import edu.wpi.first.wpilibj.XboxController;
import edu.wpi.first.wpilibj2.command.Command;
import edu.wpi.first.wpilibj2.command.Commands;
import edu.wpi.first.wpilibj2.command.button.CommandXboxController;
import edu.wpi.first.wpilibj2.command.sysid.SysIdRoutine;
import frc.robot.commands.DriveCommands;
import frc.robot.commands.ShootCommands;
import frc.robot.generated.TunerConstants;
import frc.robot.subsystems.arm.Arm;
import frc.robot.subsystems.arm.ArmIO;
import frc.robot.subsystems.arm.ArmIOScrimmage;
import frc.robot.subsystems.arm.ArmIOTalonFX;
import frc.robot.subsystems.drive.Drive;
import frc.robot.subsystems.drive.GyroIO;
import frc.robot.subsystems.drive.GyroIOPigeon2;
import frc.robot.subsystems.drive.GyroIOScrimmage;
import frc.robot.subsystems.drive.ModuleIO;
import frc.robot.subsystems.drive.ModuleIOScrimmage;
import frc.robot.subsystems.drive.ModuleIOTalonFX;
import frc.robot.subsystems.elevator.Elevator;
import frc.robot.subsystems.elevator.ElevatorIO;
import frc.robot.subsystems.elevator.ElevatorIOScrimmage;
import frc.robot.subsystems.elevator.ElevatorIOTalonFX;
import frc.robot.subsystems.shooter.Shooter;
import frc.robot.subsystems.shooter.ShooterIO;
import frc.robot.subsystems.shooter.ShooterIOScrimmage;
import frc.robot.subsystems.shooter.ShooterIOTalonFX;
import java.util.LinkedHashMap;
import java.util.Set;
import org.littletonrobotics.junction.networktables.LoggedDashboardChooser;
import scrimmage.sim.Scrimmage;

/**
 * This class is where the bulk of the robot should be declared. Since Command-based is a
 * "declarative" paradigm, very little robot logic should actually be handled in the {@link Robot}
 * periodic methods (other than the scheduler calls). Instead, the structure of the robot (including
 * subsystems, commands, and button mappings) should be declared here.
 */
public class RobotContainer {
  private static final String DEFAULT_AUTO = "Drive and Raise";
  /**
   * The test range's (scrimmage's 2026 range file) blue starting positions the shooting autos start
   * from: x 3.42 m (behind the starting line, so the robot can turn to the hub without its bumper
   * corner hitting a BUMP), facing the hub.
   */
  private static final Pose2d START_LEFT = new Pose2d(3.42, 5.56, Rotation2d.kZero);

  private static final Pose2d START_RIGHT = new Pose2d(3.42, 2.51, Rotation2d.kZero);
  /** A standing shot gives up after this long (s). */
  private static final double SHOOT_TIMEOUT = 8.0;

  // Subsystems
  private final Drive drive;
  private final Elevator elevator;
  private final Arm arm;
  private final Shooter shooter;

  // Controller
  private final CommandXboxController controller = new CommandXboxController(0);

  // Dashboard inputs
  private final LoggedDashboardChooser<Command> autoChooser;

  /** The container for the robot. Contains subsystems, OI devices, and commands. */
  public RobotContainer() {
    switch (Constants.currentMode) {
      case REAL:
        // Real robot, instantiate hardware IO implementations
        // ModuleIOTalonFX is intended for modules with TalonFX drive, TalonFX turn, and
        // a CANcoder
        drive =
            new Drive(
                new GyroIOPigeon2(),
                new ModuleIOTalonFX(TunerConstants.FrontLeft),
                new ModuleIOTalonFX(TunerConstants.FrontRight),
                new ModuleIOTalonFX(TunerConstants.BackLeft),
                new ModuleIOTalonFX(TunerConstants.BackRight));

        // The ModuleIOTalonFXS implementation provides an example implementation for
        // TalonFXS controller connected to a CANdi with a PWM encoder. The
        // implementations
        // of ModuleIOTalonFX, ModuleIOTalonFXS, and ModuleIOSpark (from the Spark
        // swerve
        // template) can be freely intermixed to support alternative hardware
        // arrangements.
        // Please see the AdvantageKit template documentation for more information:
        // https://docs.advantagekit.org/getting-started/template-projects/talonfx-swerve-template#custom-module-implementations
        //
        // drive =
        // new Drive(
        // new GyroIOPigeon2(),
        // new ModuleIOTalonFXS(TunerConstants.FrontLeft),
        // new ModuleIOTalonFXS(TunerConstants.FrontRight),
        // new ModuleIOTalonFXS(TunerConstants.BackLeft),
        // new ModuleIOTalonFXS(TunerConstants.BackRight));
        elevator = new Elevator(new ElevatorIOTalonFX());
        arm = new Arm(new ArmIOTalonFX());
        shooter = new Shooter(new ShooterIOTalonFX());
        break;

      case SIM:
        if (Scrimmage.fastMode()) {
          // scrimmage fast mode: simulated devices (no Phoenix), stepped with physics in lockstep
          drive =
              new Drive(
                  new GyroIOScrimmage(),
                  new ModuleIOScrimmage("fl", TunerConstants.FrontLeft),
                  new ModuleIOScrimmage("fr", TunerConstants.FrontRight),
                  new ModuleIOScrimmage("bl", TunerConstants.BackLeft),
                  new ModuleIOScrimmage("br", TunerConstants.BackRight));
          elevator = new Elevator(new ElevatorIOScrimmage());
          arm = new Arm(new ArmIOScrimmage());
          shooter = new Shooter(new ShooterIOScrimmage());
        } else {
          // Phoenix 6's own simulation of the real IO, its devices moved by scrimmage's physics
          // (scrimmage's Phoenix mode, on the wall clock)
          drive =
              new Drive(
                  new GyroIOPigeon2(),
                  new ModuleIOTalonFX(TunerConstants.FrontLeft),
                  new ModuleIOTalonFX(TunerConstants.FrontRight),
                  new ModuleIOTalonFX(TunerConstants.BackLeft),
                  new ModuleIOTalonFX(TunerConstants.BackRight));
          elevator = new Elevator(new ElevatorIOTalonFX());
          arm = new Arm(new ArmIOTalonFX());
          shooter = new Shooter(new ShooterIOTalonFX());
        }
        break;

      default:
        // Replayed robot, disable IO implementations
        drive =
            new Drive(
                new GyroIO() {},
                new ModuleIO() {},
                new ModuleIO() {},
                new ModuleIO() {},
                new ModuleIO() {});
        elevator = new Elevator(new ElevatorIO() {});
        arm = new Arm(new ArmIO() {});
        shooter = new Shooter(new ShooterIO() {});
        break;
    }

    // Set up auto routines: "Drive and Raise" (a 2 m path, then the elevator up, then the arm) by
    // default; the environment variable SCRIMMAGE_AUTO picks another (scrimmage scenarios set it)
    NamedCommands.registerCommand(
        "RaiseElevator", elevator.moveTo(ScrimmageConstants.ELEVATOR_AUTO_HEIGHT));
    NamedCommands.registerCommand("RaiseArm", arm.moveTo(ScrimmageConstants.ARM_AUTO_ANGLE));
    NamedCommands.registerCommand(
        "ShootWhileDriving", ShootCommands.shootWhileDriving(drive, shooter));
    NamedCommands.registerCommand("Intake", ShootCommands.intake(shooter));
    NamedCommands.registerCommand(
        "ShootStanding", ShootCommands.shootStanding(drive, shooter, SHOOT_TIMEOUT));
    // The autos for scrimmage's test ranges, not PathPlanner's: standing shots from the 2026
    // field's left and right starting positions, each placed there (pose set) since no path starts
    // them, and the drives over the 2026 BUMP (its 2026-bump range)
    var rangeAutos = new LinkedHashMap<String, Command>();
    rangeAutos.put("Shoot Left", standingShot(START_LEFT));
    rangeAutos.put("Shoot Right", standingShot(START_RIGHT));
    rangeAutos.put("Over the Bump", overTheBump(ScrimmageConstants.BUMP_SPEED));
    rangeAutos.put(
        "Over the Bump, Elevator Up",
        Commands.sequence(
            elevator.moveTo(ScrimmageConstants.ELEVATOR_BUMP_HEIGHT),
            overTheBump(ScrimmageConstants.BUMP_SPEED_ELEVATOR_UP)));
    String chosen = System.getenv().getOrDefault("SCRIMMAGE_AUTO", DEFAULT_AUTO);
    boolean pathPlannerAuto = AutoBuilder.getAllAutoNames().contains(chosen);
    if (!pathPlannerAuto && !rangeAutos.containsKey(chosen)) {
      DriverStation.reportError(
          "SCRIMMAGE_AUTO=" + chosen + ": no such auto; running " + DEFAULT_AUTO, false);
      chosen = DEFAULT_AUTO;
      pathPlannerAuto = true;
    }
    autoChooser =
        new LoggedDashboardChooser<>(
            "Auto Choices", AutoBuilder.buildAutoChooser(pathPlannerAuto ? chosen : DEFAULT_AUTO));
    for (var auto : rangeAutos.entrySet()) {
      if (auto.getKey().equals(chosen)) {
        autoChooser.addDefaultOption(auto.getKey(), auto.getValue());
      } else {
        autoChooser.addOption(auto.getKey(), auto.getValue());
      }
    }
    System.out.println("auto: " + chosen);

    // Set up SysId routines
    autoChooser.addOption(
        "Drive Wheel Radius Characterization", DriveCommands.wheelRadiusCharacterization(drive));
    autoChooser.addOption(
        "Drive Simple FF Characterization", DriveCommands.feedforwardCharacterization(drive));
    autoChooser.addOption(
        "Drive SysId (Quasistatic Forward)",
        drive.sysIdQuasistatic(SysIdRoutine.Direction.kForward));
    autoChooser.addOption(
        "Drive SysId (Quasistatic Reverse)",
        drive.sysIdQuasistatic(SysIdRoutine.Direction.kReverse));
    autoChooser.addOption(
        "Drive SysId (Dynamic Forward)", drive.sysIdDynamic(SysIdRoutine.Direction.kForward));
    autoChooser.addOption(
        "Drive SysId (Dynamic Reverse)", drive.sysIdDynamic(SysIdRoutine.Direction.kReverse));

    // Configure the button bindings
    configureButtonBindings();
  }

  /** Places the robot at {@code start} (its odometry), then shoots what it holds, standing. */
  private Command standingShot(Pose2d start) {
    return Commands.sequence(
        Commands.runOnce(() -> drive.setPose(start), drive),
        ShootCommands.shootStanding(drive, shooter, SHOOT_TIMEOUT));
  }

  /**
   * Drives straight ahead over the bump at {@code speed} (m/s; in simulation SCRIMMAGE_BUMP_SPEED
   * replaces it), {@link ScrimmageConstants#BUMP_DRIVE_DISTANCE} along the field's x. Prints "bump:
   * driving at <speed> m/s" as it starts.
   */
  private Command overTheBump(double speed) {
    return Commands.defer(
        () -> {
          double chosen = ScrimmageConstants.bumpSpeed(speed);
          return Commands.sequence(
              Commands.print(String.format("bump: driving at %.2f m/s", chosen)),
              DriveCommands.driveStraight(
                  drive,
                  chosen,
                  ScrimmageConstants.BUMP_DRIVE_DISTANCE,
                  ScrimmageConstants.BUMP_ACCELERATION));
        },
        Set.of(drive));
  }

  /**
   * Use this method to define your button->command mappings. Buttons can be created by
   * instantiating a {@link GenericHID} or one of its subclasses ({@link
   * edu.wpi.first.wpilibj.Joystick} or {@link XboxController}), and then passing it to a {@link
   * edu.wpi.first.wpilibj2.command.button.JoystickButton}.
   */
  private void configureButtonBindings() {
    // Default command, normal field-relative drive
    drive.setDefaultCommand(
        DriveCommands.joystickDrive(
            drive,
            () -> -controller.getLeftY(),
            () -> -controller.getLeftX(),
            () -> -controller.getRightX()));

    // Lock to 0° when A button is held
    controller
        .a()
        .whileTrue(
            DriveCommands.joystickDriveAtAngle(
                drive,
                () -> -controller.getLeftY(),
                () -> -controller.getLeftX(),
                () -> Rotation2d.kZero));

    // Switch to X pattern when X button is pressed
    controller.x().onTrue(Commands.runOnce(drive::stopWithX, drive));

    // Reset gyro to 0° when B button is pressed
    controller
        .b()
        .onTrue(
            Commands.runOnce(
                    () ->
                        drive.setPose(
                            new Pose2d(drive.getPose().getTranslation(), Rotation2d.kZero)),
                    drive)
                .ignoringDisable(true));
  }

  /**
   * Use this to pass the autonomous command to the main {@link Robot} class.
   *
   * @return the command to run in autonomous
   */
  public Command getAutonomousCommand() {
    return autoChooser.get();
  }
}
