# Pneumatics (air cylinders)

Scrimmage simulates a robot's air system: the tanks, the compressor and its pressure switch, the regulator, and each
double-acting cylinder with its valve. A cylinder pushes with the air's pressure on its piston, as fast as air can
flow through its valve, and the air it uses comes out of the tanks. You can see whether a cylinder pushes hard enough,
how fast it fires, whether the air lasts a match, and what the compressor costs the battery.

**Every pneumatic number here is an estimate (UNCALIBRATED).** The defaults come from FRC rules, vendor data and
common FRC parts (each source is below), not from measuring a real robot. Runs that use cylinders say so in their
notes: `Pneumatic numbers are estimates (UNCALIBRATED): see docs/pneumatics.md.` [Measure your own](#measure-your-own)
says how to check them against your robot.

**Pressures are in psi.** Everything else in Scrimmage is in meters, kilograms, seconds and degrees, but every FRC
pneumatic part and rule is written in psi, so pressures are the one exception. Scrimmage's psi is gauge pressure (what
a gauge on the robot reads, 0 at the atmosphere).

Contents: [Adding a cylinder](#adding-a-cylinder), [How the air works](#how-the-air-works),
[The numbers and where they come from](#the-numbers-and-where-they-come-from),
[The air budget: `scrimmage air`](#the-air-budget-scrimmage-air), [Measure your own](#measure-your-own),
[Robot code](#robot-code), [Reading the air](#reading-the-air), [What isn't simulated](#what-isnt-simulated),
[Errors and warnings](#errors-and-warnings).

## Adding a cylinder

A cylinder is the stock part `cylinder`, set in a robot config or in the Robot tab's stock part form. Its `valve` slot
takes the solenoid that drives it. A robot with cylinders and no `[pneumatics]` section gets the default air system.

```toml
[[part]]
name = "kicker"
stock = "cylinder"            # 1-1/16 in bore, 5/16 in rod, 6 in stroke by default
attach = "drive.top"
pos = [0.2, 0, 0.1]           # the barrel lies along the part's +x; the rod pushes out toward +x
ids = { valve = "PCM:3" }     # a single solenoid on channel 3 of CTRE PCM 0

[pneumatics]                  # optional: these are the defaults
tanks = 2
tank_ml = 574
compressor = "VIAIR 90C"
working_psi = 60
start_psi = 120
```

The cylinder's two forms (a free rod with its own mount, or a rod pinned to another part, such as a piston that lifts
an arm), its parameters, the valve IDs and every `[pneumatics]` key are in [parts.md](parts.md#cylinder). The Test tab
works a cylinder with no robot code and no valve ID: see [the Test tab](test-tab.md#pneumatics).
`examples/blocks/pneumatic` is a worked example, an arm lifted by a cylinder.

## How the air works

**The stored side.** The tanks hold air at up to 120 psi. Before a match they are charged (the run starts at
`start_psi`, 120 by default). The compressor fills them, and a pressure switch turns it on below 95 psi and off at 115
psi, so it doesn't cycle on and off every moment. The compressor runs only while the robot is enabled and the robot
program has made the module its valves are on (its closed loop is on unless the program turns it off; see [Robot
code](#robot-code)). Under the Test tab it runs as if the closed loop were on, and the switch still starts and stops it.
The higher the pressure, the less air the compressor pushes each second, and the more current it draws.

**The regulator.** The cylinders don't get the tanks' pressure. A regulator lowers it to the working pressure, 60 psi
by default. When the tanks fall below the working pressure, the cylinders get only what the tanks have, and they push
weaker from then on.

**A cylinder.** Each cylinder is double-acting: it has two chambers, one on each side of its piston.

- **Extending** fills the cap side (behind the piston) from the regulator and lets the rod side out to the air.
- **Retracting** is the reverse. It pushes a little weaker, because the rod takes up some of the piston's area.
- **The force** is each chamber's pressure times the area it pushes on, less the seal friction. At 60 psi the stock
  cylinder (1-1/16 in bore, 5/16 in rod) pushes about 237 N (53 lbf) out and 216 N (49 lbf) in, less its 8 N of
  friction.
- **The speed** comes from how fast air can flow through the valve into one chamber and out of the other. The flow
  follows ISO 6358, the standard way valve makers rate valves: a valve's flow rises with the pressure drop across it
  until it is *choked* (the air moves at the speed of sound in the valve), and then it is as fast as it gets. The stock
  cylinder with nothing on its rod extends all the way in about 0.12 s.
- **End stops.** The rod bottoms out at each end of its stroke.

**The valve.** Each valve has five ports (air in, two to the cylinder, two exhausts) and two positions, so it always
fills one side of the cylinder while it lets the other side out (valve makers call this a 5/2 valve):

- A **single solenoid** has a spring: on extends, off retracts.
- A **double solenoid** has two coils: forward extends and reverse retracts. With both off it stays where it was (it
  is detented), and at the start that is retracted. With both on it also stays where it was, with a warning.
- `invert = true` in a valve's ID table swaps extend and retract (for a cylinder plumbed the other way round).
- A disabled robot's solenoids are off, as on a real robot: a single solenoid's cylinder retracts (extends, when the
  valve is inverted), and a double solenoid's stays where it was. The Test tab's **set** *cylinder* **to off** does
  the same.

**Dead volume.** Each port of a cylinder has a little air that the piston never sweeps: the port itself, its fitting,
and the tube back to the valve. Scrimmage counts 5 cm³ at each port (about 0.3 m of 5/32 in inside-diameter tube plus
the fitting; an estimate, UNCALIBRATED). It matters for two things. Filling it costs a little air every stroke. And
the air in a cylinder is a spring: squeezed into only the dead volume at the end of a stroke, it is a stiff one.

**Keeping the physics steady.** A stiff air spring on a light rod can shake itself apart in a simulation that steps
every 1 ms (the real one is smooth). So Scrimmage adds a small mass along each cylinder's line: about 0.02 kg on the
stock 1-1/16 in bore, and about 0.2 kg on a 2 in bore. It stands for the rod's own mass and the air's lag in the tube,
and it doesn't change the force. On the stock cylinder it is small next to the rod's own 0.1 kg. Every chamber also
steps 10 times per physics step, and a chamber's fill stops at the pressure it fills from, so it can never overshoot.

**Air and the battery.** Air that fills a chamber comes out of the tanks. The compressor's current (about 7 A at 0 psi
rising to about 11 A at 100 psi) is drawn from the battery like a motor's, so it lowers the battery voltage while it
runs.

**One temperature.** The air stays at 20 °C. Real air warms when squeezed and cools when it expands, which Scrimmage
leaves out (an *isothermal* model).

## The numbers and where they come from

Each is an estimate (UNCALIBRATED) unless it is an FRC rule.

| Number | Default | Set with | Source |
|---|---|---|---|
| Stored pressure at the start | 120 psi | `[pneumatics] start_psi` | FRC allows at most 120 psi stored: the [FRC Pneumatics Manual](https://www.firstinspires.org/hubfs/web/program/frc/resources/pneumatics-manual.pdf). |
| Working pressure | 60 psi | `[pneumatics] working_psi` | FRC allows at most 60 psi on the working side: the [FRC Pneumatics Manual](https://www.firstinspires.org/hubfs/web/program/frc/resources/pneumatics-manual.pdf). |
| Tanks | 2 tanks of 574 mL | `[pneumatics] tanks`, `tank_ml` | The Clippard AVT-PP-35 tank common in FRC, 574 mL ([AndyMark am-2649](https://andymark.com/am-2649)). Two is our estimate of a usual robot. |
| Compressor flow and current | VIAIR 90C: 0.88 cfm of free air at 7 A at 0 psi, 0.22 cfm at 11 A at 100 psi, a straight line between, carried on to 120 psi | `[pneumatics] compressor` | AndyMark's bench test of the VIAIR 90C ([AndyMark am-2005](https://andymark.com/am-2005)). The straight line between the two points is our estimate. |
| Pressure switch | on below 95 psi, off at 115 psi | `[pneumatics] switch_on_psi`, `switch_off_psi` | An estimate from the Nason SM-2B-115R switch FRC kits use (it opens at about 115 psi). |
| Cylinder size | 1-1/16 in bore, 5/16 in rod, 6 in stroke | the `cylinder` part's `bore`, `rod`, `stroke` | Common Bimba sizes in FRC. |
| Seal friction | 8 N | the part's `friction` | An estimate. |
| Barrel and rod mass | 0.25 kg and 0.1 kg | the part's `mass`, `rod_mass` | Estimates. |
| Valve flow | sonic conductance C = 0.8 dm³/(s·bar) (about Cv 0.2), critical ratio b = 0.3 | the part's `conductance`, in m³/(s·Pa): 0.8 dm³/(s·bar) is `8e-9` | The ISO 6358 flow model. The value is an estimate for FRC's small 5/2 valves (the SMC and Festo valves in the kits). |
| Dead volume | 5 cm³ at each port | not a setting | An estimate: the port, its fitting and about 0.3 m of 5/32 in inside-diameter tube. |
| Match length for the budget | 150 s | `scrimmage air --match-seconds` | An FRC match's 2 min 30 s. |

Scrimmage refuses a working pressure above 60 psi, or a start or switch pressure above 120 psi, and the message names
the rule ("FRC allows at most 60 psi on the working side").

## The air budget: `scrimmage air`

FRC teams often work out by hand how many times their cylinders can fire in a match. `scrimmage air` does that
sum for a robot config:

```bash
scrimmage air examples/blocks/pneumatic/arm-standin.toml
```

The pneumatic example's robot (one 3/4 in bore, 8 in stroke cylinder named `lift`, and the default air) prints:

```
Working pressure 60 psi; the switch stops the compressor at 115 psi.
Free air for one cycle (extend and retract):
  lift: 0.597 L
Stored air above the working pressure: 4.30 L
Cycles from the tanks alone: 7
Compressor air in a 150 s match: 13.2 L
Cycles per match, tanks and compressor: 29
Estimates (UNCALIBRATED): see docs/pneumatics.md.
```

The tanks' 4.30 L pay for 4.30 / 0.597, about 7 cycles, and with the compressor's 13.2 L, (4.30 + 13.2) / 0.597,
about 29. The cycle counts are rounded to whole cycles (4.8 shows as 5). The stock cylinder (1-1/16 in bore, 6 in
stroke) takes 0.889 L a cycle: 5 from the tanks, 20 in a match.

- **Free air** is air measured at the atmosphere's pressure, the way compressors and tanks are rated.
- **One cycle** is one extend and one retract, each side filled all the way to the working pressure. The piston's side
  starts empty (at the atmosphere), so filling it to 60 psi takes 60 + 14.7 psi worth of air. The dead volume is
  already at the atmosphere, so it takes only the 60.
- **Stored air** is what the tanks hold between the switch's top (115 psi) and the working pressure: below the
  working pressure, the cylinders push weaker.
- **The compressor's air** is its flow halfway between the switch points, for the whole match.
- `--match-seconds 135` (say) changes the match length.

It is a quick sum, not a run: a real cylinder whose valve switches before the stroke ends uses less than a full
cycle, and a run that starts at 120 psi has a little more than the budget counts. That is why the pneumatic example's
test lifts its arm 10 times on the tanks where the budget says 7; its README compares the two.

## Measure your own

The defaults are estimates. Two checks with things every team has tell you how close they are for your robot.

**How fast a cylinder fires.** Film the cylinder firing with a phone's slow-motion video (240 frames a second is
common), with the robot enabled and at its working pressure. Count the frames from the first movement to the end of
the stroke and divide by the frame rate. Then try the same in Scrimmage: a Test-tab run with a **set** block and a
**wait until** *cylinder is extended* block gives the time (the chart's `<name>.extension` shows the stroke). If the
real one is slower, the valve or the tubing lets less air through than the estimate: lower the part's `conductance`
roughly in proportion (half as fast, about half the conductance) and run again. Put the real load on the rod in both,
since a heavy arm is slower than a bare rod.

**How much air a cycle uses.** With the compressor off (unplugged, or `disable()` on it in robot code) and the tanks
full, read the gauge on the stored side. Fire the cylinder N times (10 is enough), each stroke all the way, and read
the gauge again. The free air one cycle used is

    tank liters x (pressure drop in psi) / 14.7 / N

For two 574 mL tanks (1.148 L) and a 20 psi drop over 10 cycles, that is 1.148 x 20 / 14.7 / 10 = 0.16 L a cycle.
Compare it with the cycle line of `scrimmage air`. Count every tank on the stored side, and read the gauge after the
air settles for a few seconds (air leaving a tank cools it, so the gauge creeps back up a little as it warms).

**The compressor.** Time how long your compressor takes to fill the tanks from empty until it stops. The default
system (two 574 mL tanks and a VIAIR 90C) takes about 50 s in Scrimmage. To try your own tanks, run a Test-tab test
whose robot has `start_psi = 0` and that waits until the stored air pressure is above 114 psi; filling takes longer than a
wait's usual 10 s limit, so give that block `timeout = 120` and the test `time_limit = 150` in the test file.

When you have measured a number, set it in the config (the cylinder's parameters, the `[pneumatics]` keys) and keep a
note of how you measured it.

## Robot code

Robot code drives the valves with WPILib's `Solenoid` and `DoubleSolenoid`, and the compressor with `Compressor` or
`PneumaticHub`, as on a real robot. Scrimmage reads the solenoids' outputs and sends back the pressure switch, whether
the compressor is on and its current.

**The valve's ID names the module and its channels.** It must match what the robot code makes:

| Robot code | The valve's ID |
|---|---|
| `Solenoid(PneumaticsModuleType.CTREPCM, 3)` (PCM 0) | `"PCM:3"` |
| `DoubleSolenoid(PneumaticsModuleType.CTREPCM, 4, 5)` (PCM 0) | `"PCM:4,5"` (forward, reverse) |
| `Solenoid(PneumaticsModuleType.REVPH, 3)` (Pneumatic Hub 1) | `"PH:3"` |
| `DoubleSolenoid(PneumaticsModuleType.REVPH, 4, 5)` (Pneumatic Hub 1) | `"PH:4,5"` |
| `Solenoid(2, PneumaticsModuleType.CTREPCM, 3)` (PCM 2) | `{ pcm = 2, channel = 3 }` |
| `DoubleSolenoid(2, PneumaticsModuleType.REVPH, 4, 5)` (Pneumatic Hub 2) | `{ ph = 2, forward = 4, reverse = 5 }` |

The short forms use WPILib's default module numbers (0 for a PCM, 1 for a Pneumatic Hub). A table takes
`invert = true` too. All of a robot's valves must be on one kind of module (all PCM or all Pneumatic Hub). The full
table of ID forms is in [parts.md](parts.md#valve-ids).

**The compressor runs on its own.** A module's closed loop is on as soon as the robot program makes the module (any
`Solenoid` or `DoubleSolenoid` on it does), so the compressor runs whenever the robot is enabled, and the pressure
switch starts and stops it. You don't need a `Compressor` object or `enableDigital()` for that; `disable()` (or
`disableCompressor()` on a `PneumaticHub`) turns it off. A program that never makes the module never runs the
compressor, which is right: the real one wouldn't either. Scrimmage runs the compressor only for a module that one of
the robot's valves is on, so a compressor on a module with no valves bound to it doesn't run.

**A CTRE PCM** works with the usual RobotPy or Java setup: the HALSim WebSocket client carries its solenoids. RobotPy:

```python
import wpilib

PCM = wpilib.PneumaticsModuleType.CTREPCM

class MyRobot(wpilib.TimedRobot):
    def robotInit(self):
        self.kicker = wpilib.Solenoid(PCM, 3)               # ids = { valve = "PCM:3" }
        self.claw = wpilib.DoubleSolenoid(PCM, 4, 5)        # ids = { valve = "PCM:4,5" }
        self.compressor = wpilib.Compressor(PCM)            # optional: to read it back, or to disable() it
        self.compressor.enableDigital()                     # optional: the closed loop is already on

    def teleopPeriodic(self):
        self.kicker.set(True)                               # extend
        self.claw.set(wpilib.DoubleSolenoid.Value.kReverse) # retract
```

Java is the same:

```java
private final Solenoid kicker = new Solenoid(PneumaticsModuleType.CTREPCM, 3);
private final DoubleSolenoid claw = new DoubleSolenoid(PneumaticsModuleType.CTREPCM, 4, 5);
private final Compressor compressor = new Compressor(PneumaticsModuleType.CTREPCM);
// optional, as the closed loop is already on: compressor.enableDigital();
```

**A REV Pneumatic Hub needs the lockstep extension, version 0.7.0 or later**, for RobotPy and Java alike: the HALSim
WebSocket client carries nothing for a hub, so the extension relays it. Install it with `pip install ./lockstep`
(reinstall it after updating Scrimmage). RobotPy also needs `--scrimmage-lockstep` in the `[robot_program]` command;
Java's Gradle init script loads the extension when it is installed. A robot with valves on a hub refuses to start
with an older extension, or with none, and says how to install it. The code is the same with `REVPH` in place of
`CTREPCM`, or with the hub itself:

```python
self.hub = wpilib.PneumaticHub(1)
self.hub.enableCompressorDigital()                          # optional: the hub's closed loop is already on
self.claw = self.hub.makeDoubleSolenoid(4, 5)               # ids = { valve = "PH:4,5" }
```

Robots with valves only on a PCM keep working with lockstep 0.6.0.

**What robot code reads back.**

- `getPressureSwitchValue()` (`Compressor`) and `getPressureSwitch()` (`PneumaticHub`) are true while the switch calls
  for air (WPILib's "not full").
- `isEnabled()` (`Compressor`) and `getCompressor()` (`PneumaticHub`) say whether the compressor is running.
- `getCurrent()` (`Compressor`) and `getCompressorCurrent()` (`PneumaticHub`) read its current in amps.
- **`getPressure()` reads 0 in simulation.** WPILib's simulation has no way to set a Pneumatic Hub's analog pressure
  sensor, so robot code that reads the pressure (or runs `enableCompressorAnalog` or `enableCompressorHybrid`) can't
  see the simulated air. Use the digital switch in simulation, or read `air.psi` in a Test-tab run.

## Reading the air

- **The Test tab** has the signals `air.psi` (the tanks), `air.working_psi` (after the regulator) and each cylinder's
  `<name>.extension` in inches, and the run's air line. See [the Test tab](test-tab.md#pneumatics).
- **Agents (MCP).** `get_state` on a robot with cylinders has a `pneumatics` block:

| Key | Meaning |
|---|---|
| `stored_psi` | the tanks' pressure, psi |
| `working_psi` | the regulator's output, psi (the tanks' pressure when they are below the working pressure) |
| `compressor_on` | whether the compressor is running |
| `cylinders` | each cylinder's rod extension in meters, by its part name: `{ "kicker": { "extension_m": 0.1524 } }` (the Test tab's `<name>.extension` signal is in inches) |

## What isn't simulated

- Single-acting cylinders, rotary actuators and vacuum.
- More than one air system on a robot.
- Flow-control valves on the ports (a cylinder's flow is the one `conductance`) and leaks.
- Temperature: the air stays at 20 °C.
- A Pneumatic Hub's analog pressure sensor (`getPressure()` reads 0).
- Pneumatics on the [component bench](bench.md).

## Errors and warnings

| Message | What to do |
|---|---|
| `kicker.valve has no device ID, so robot code can't move it (the Test tab's blocks still can)` | Robot code can't reach a valve without an ID: give the slot one. Under the Test tab the valve needs none. |
| `... is on a PCM and ... is on a PH: one robot's valves must all be on one kind of module` | Put every valve on PCMs, or every valve on Pneumatic Hubs. |
| `...: both of the double solenoid's channels (4 and 5) are on, so the valve holds where it is; turn one off` | The robot code set both coils of a double solenoid. Set one. |
| `... is on a REV Pneumatic Hub, which needs the lockstep extension` | Install it (`pip install ./lockstep`), and for RobotPy add `--scrimmage-lockstep` to the command. |
| `...: the robot never opened Pneumatic Hub 2 (it opened Pneumatic Hub 1)` | The hub's number in the valve's ID doesn't match the robot code's. |
| `The working pressure is 80 psi; FRC allows at most 60 psi on the working side` | Keep `working_psi` at 60 or below (and stored pressures at 120 or below). |
| `the tanks fell below the 60 psi working pressure at 2.0 s: the cylinders pushed weaker from then on` | A Test-tab run used the air faster than the compressor made it. Use a smaller bore, fewer strokes, or more tanks; `scrimmage air` shows the budget. |
