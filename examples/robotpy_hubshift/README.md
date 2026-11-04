# Is our hub active? A 2026 hub-shift robot, tested with match data

In 2026 (REBUILT) the two alliances' hubs take turns in Teleop. About 3 s after Auto ends, the field sends the robot
one letter of game data, `R` or `B`: the alliance whose hub goes inactive first. Robot code puts that together with
the match time and its own alliance to know whether its hub is active right now.

`robot.py` does that with `is_hub_active()`. It follows WPILib's example on the "2026 Game Data Details" page
(docs.wpilib.org), written in Python. It reads `DriverStation.getGameSpecificMessage()`, `DriverStation.getMatchTime()`
and `DriverStation.getAlliance()`, and each time its answer changes it prints one line, such as
`hub inactive (match time 130.000 s)`.

The question the scenario answers: as red1, with the field's letter `R`, does the robot say its hub is inactive in
Shifts 1 and 3, and active everywhere else?

It's the worked example for [match data](../../docs/match.md).

## The files

| File | What it is |
|---|---|
| `robot.py` | the robot: `is_hub_active()` and the prints. Nothing in it knows it is simulated. |
| `hubshift.toml` | the robot config: a bare chassis (the robot doesn't drive) and `robot.py` as its program |
| `hubshift.scenario.toml` | one 2026 match: Auto, the pause, then Teleop with `game_data = "R"`, and a console check in every step |
| `pyproject.toml` | the RobotPy project file |

## The rules the robot follows

The named alliance's hub is inactive first, so it is active in Shifts 2 and 4. Both hubs are always active in Auto,
in the transition at the start of Teleop and in the End Game. By match time left in Teleop (140 s):

| Window | Match time left | red1 with `R` |
|---|---|---|
| transition | 140 to 130 s | active |
| Shift 1 | 130 to 105 s | inactive |
| Shift 2 | 105 to 80 s | active |
| Shift 3 | 80 to 55 s | inactive |
| Shift 4 | 55 to 30 s | active |
| End Game | 30 to 0 s | active |

Like WPILib's example, `is_hub_active()` says active in Teleop before any game data arrives (it is likely early in
Teleop, when both hubs are active), and inactive while the robot is disabled.

## Running it

```bash
scrimmage run examples/robotpy_hubshift/hubshift.scenario.toml
```

The scenario's `[match]` sets `station = "red1"` and `timer = true`, so the match time counts down: from 20 s in
Auto and from 140 s in Teleop. The first Teleop step sets `game_data = "R"`, and the letter stays for the rest of the
match. Teleop's steps add up to its 140 s.

The robot's answer changes on the match time, so the scenario gives each change a short 0.2 s step of its own,
centered on it: "shift 1 starts" runs from 130.1 to 129.9 s left and checks that the console says `hub inactive`
there. That leaves 0.1 s on each side, more than the robot's 20 ms loop, so the change can't land in the wrong step.
The long steps between check that nothing changes: "shift 1" (129.9 to 105.1 s) checks the console never says
`hub active`.

The run passes, and the robot's console shows each change where the table puts it:

```
hub inactive (match time -1.000 s)      before the match: disabled, no match time yet
hub active (match time 20.000 s)        Auto starts
hub inactive (match time 0.000 s)       the pause: disabled, the time holds at 0
game data 'R'
hub active (match time 140.000 s)       Teleop starts
hub inactive (match time 130.000 s)     Shift 1
hub active (match time 105.000 s)       Shift 2
hub inactive (match time 80.000 s)      Shift 3
hub active (match time 55.000 s)        Shift 4, and no change at 30 s: the End Game is active too
```

The report's note that `drive.gyro has no device ID` is expected: this robot has no gyro.

## Try a change: play blue1

In `hubshift.scenario.toml`, change `station = "red1"` to `station = "blue1"` and run it again. Blue's hub is active
first after red's `R`, so the robot changes at 105, 80, 55 and 30 s, the other way round. The four checks at the
shift starts ("shift 1 starts" to "shift 4 starts") fail, and the rest pass.

To play the match by hand, open `hubshift.toml` in the Drive tab (`scrimmage edit examples/robotpy_hubshift/hubshift.toml`),
type `R` in the Game data box and enable in Teleop. The HUD shows the match time, and the console the robot's answers.
