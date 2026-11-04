"""Test robot for scrimmage's match data test (tests/test_match_robot_e2e.py): it reads what the Driver Station sends.

Once a second in teleop it prints one line: "match data=<game data> alliance=<alliance> location=<n> fms=<bool>
time=<s>". Once a second in any mode it prints "ds mode=<disabled|auto|teleop|test> axis0=<joystick 0's axis 0, or
none>" (the real Driver Station test, tests/test_real_ds_robot_e2e.py).
"""

import wpilib
from wpilib import DriverStation


class MyRobot(wpilib.TimedRobot):
    def robotInit(self) -> None:
        self.loops = 0
        self.robot_loops = 0

    def robotPeriodic(self) -> None:
        self.robot_loops += 1
        if self.robot_loops % 50 == 1:
            mode = ("disabled" if DriverStation.isDisabled() else "auto" if DriverStation.isAutonomous()
                    else "test" if DriverStation.isTest() else "teleop")
            axis = (f"{DriverStation.getStickAxis(0, 0):.2f}" if DriverStation.getStickAxisCount(0) > 0
                    else "none")
            print(f"ds mode={mode} axis0={axis}", flush=True)

    def teleopPeriodic(self) -> None:
        self.loops += 1
        if self.loops % 50 == 1:
            alliance = DriverStation.getAlliance()
            print(f"match data={DriverStation.getGameSpecificMessage()!r} "
                  f"alliance={alliance.name if alliance is not None else None} "
                  f"location={DriverStation.getLocation()} fms={DriverStation.isFMSAttached()} "
                  f"time={DriverStation.getMatchTime():.3f}", flush=True)
