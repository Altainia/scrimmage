"""Test robot for scrimmage's match data test (tests/test_match_robot_e2e.py): it reads what the Driver Station sends.

Once a second in teleop it prints one line: "match data=<game data> alliance=<alliance> location=<n> fms=<bool>
time=<s>".
"""

import wpilib
from wpilib import DriverStation


class MyRobot(wpilib.TimedRobot):
    def robotInit(self) -> None:
        self.loops = 0

    def teleopPeriodic(self) -> None:
        self.loops += 1
        if self.loops % 50 == 1:
            alliance = DriverStation.getAlliance()
            print(f"match data={DriverStation.getGameSpecificMessage()!r} "
                  f"alliance={alliance.name if alliance is not None else None} "
                  f"location={DriverStation.getLocation()} fms={DriverStation.isFMSAttached()} "
                  f"time={DriverStation.getMatchTime():.3f}", flush=True)
