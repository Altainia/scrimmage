"""Is our hub active? A 2026 (REBUILT) robot that works it out the way WPILib's "2026 Game Data Details" example does.

In Teleop the two hubs take turns. About 3 s after Auto ends, the field sends one letter of game data, "R" or "B": the
alliance whose hub is inactive first. Its hub is inactive in Shifts 1 and 3 and active in Shifts 2 and 4; the other
alliance's hub is the opposite. Both hubs are active in Auto, in the transition at the start of Teleop and in the End
Game. The robot knows where it is in the match from the match time (seconds left in the phase) and which alliance it
is on from the Driver Station.

Nothing here knows it is being simulated. Each time the answer changes, the robot prints one line:
"hub active (match time 140.000 s)" or "hub inactive (...)", and it prints the game data when that changes.
"""

import wpilib
from wpilib import DriverStation


def is_hub_active() -> bool:
    """WPILib's isHubActive() example, in Python."""
    alliance = DriverStation.getAlliance()
    if alliance is None:          # no Driver Station yet: we don't know our alliance
        return False
    if DriverStation.isAutonomousEnabled():    # both hubs are always active in Auto
        return True
    if not DriverStation.isTeleopEnabled():    # disabled (or Test): no hub
        return False

    match_time = DriverStation.getMatchTime()
    game_data = DriverStation.getGameSpecificMessage()
    if game_data == "":           # no game data yet: it is likely early in Teleop, when both hubs are active
        return True
    if game_data[0] == "R":
        red_inactive_first = True
    elif game_data[0] == "B":
        red_inactive_first = False
    else:                         # data we don't understand: assume active
        return True

    # Is our hub active in Shift 1? Only if the other alliance's hub goes inactive first.
    if alliance == DriverStation.Alliance.kRed:
        shift1_active = not red_inactive_first
    else:
        shift1_active = red_inactive_first

    if match_time > 130:          # the transition (140-130 s left)
        return True
    if match_time > 105:          # Shift 1 (130-105 s)
        return shift1_active
    if match_time > 80:           # Shift 2 (105-80 s)
        return not shift1_active
    if match_time > 55:           # Shift 3 (80-55 s)
        return shift1_active
    if match_time > 30:           # Shift 4 (55-30 s)
        return not shift1_active
    return True                   # the End Game (30-0 s)


class MyRobot(wpilib.TimedRobot):
    def robotInit(self) -> None:
        self.hub_active = None
        self.game_data = ""

    def robotPeriodic(self) -> None:
        game_data = DriverStation.getGameSpecificMessage()
        if game_data != self.game_data:
            self.game_data = game_data
            print(f"game data {game_data!r}", flush=True)
        active = is_hub_active()
        if active != self.hub_active:
            self.hub_active = active
            print(f"hub {'active' if active else 'inactive'} (match time {DriverStation.getMatchTime():.3f} s)",
                  flush=True)
