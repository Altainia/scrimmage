"""Starting files for `scrimmage edit`: robot configs (`--new swerve|swerve-rev|differential|standin`) and scenarios
(`--new-scenario --robot CONFIG`)."""

from __future__ import annotations

import json

_HEADER = """\
# {title}
# Edit it with `scrimmage edit {{this file}}`, or by hand: the assembler only touches [[part]] tables.

[model]
timestep = 0.001

[robot]
# Where the robot starts: pick it in the Robot tab ("Where it starts"), which writes
# start_pose = [x (m), y (m), heading (deg)], field coordinates from the blue corner.

[battery]
nominal_voltage = 12.5
resistance = 0.02

[lockstep]
step = 0.005

# How scrimmage starts your robot program (uncomment and adjust; "{{python}}" = the Python running scrimmage).
# [robot_program]
# command = ["{{python}}", "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]
# cwd = "../my_robot_project"
# Or use the Robot code panel in `scrimmage edit`: it writes this for Java or Python.

[telemetry]
odometry = "/SmartDashboard/Field/Robot"   # NT topic with the robot's own pose estimate (Field2d)

# ---- Parts ---------------------------------------------------------------------------
# Each [[part]] is a stock part (or path = "<part folder>") with its parameters, where it attaches
# ("<instance>.<mount>"), and the device ID of each slot in `ids`.

"""

_SWERVE = """\
[[part]]
name = "drive"
stock = "chassis"
ids = { gyro = "Pigeon2:13" }

[[part]]
name = "fl"
stock = "swerve/MK4i"
ratio = "L2"
attach = "drive.front_left"
ids = { drive = "TalonFX:1", steer = "TalonFX:2", encoder = "CANcoder:9" }

[[part]]
name = "fr"
stock = "swerve/MK4i"
ratio = "L2"
attach = "drive.front_right"
drive_invert = true       # right side
ids = { drive = "TalonFX:3", steer = "TalonFX:4", encoder = "CANcoder:10" }

[[part]]
name = "bl"
stock = "swerve/MK4i"
ratio = "L2"
attach = "drive.back_left"
ids = { drive = "TalonFX:5", steer = "TalonFX:6", encoder = "CANcoder:11" }

[[part]]
name = "br"
stock = "swerve/MK4i"
ratio = "L2"
attach = "drive.back_right"
drive_invert = true       # right side
ids = { drive = "TalonFX:7", steer = "TalonFX:8", encoder = "CANcoder:12" }
"""

# REV's MAXSwerve Java template: kTrackWidth = kWheelBase = 26.5 in between module centers (a 28 in frame with
# the modules 0.75 in in from each edge), CAN IDs as in its Constants.java, an absolute encoder on each steer SPARK
# with the magnet where the template's chassis angular offsets expect it, and a WPILib ADIS16470_IMU (SimDevice
# "Gyro:ADIS16470[0]", counter-clockwise-positive yaw on its Z axis: gyro_angle_z / gyro_rate_z).
_SWERVE_REV = """\
[[part]]
name = "drive"
stock = "chassis"
length = 0.7112           # 28 in frame
width = 0.7112
module_inset = 0.01905    # 0.75 in: modules 26.5 in (0.6731 m) apart
ids = { gyro = { device = "ADIS16470[0]", angle = "gyro_angle_z", rate = "gyro_rate_z", cw_positive = false } }

[[part]]
name = "fl"
stock = "swerve/MAXSwerve"
ratio = "14T"             # 4.71, the template's 14-tooth pinion
attach = "drive.front_left"
# The template's encoders are zeroed with each module turned to its corner (kFrontLeftChassisAngularOffset etc.):
# `offset` puts each magnet there (rotations; front left -pi/2 -> 0.25, back left pi -> -0.5, back right pi/2 -> -0.25).
ids = { drive = "SparkMax:11", steer = "SparkMax:10", encoder = { rev = "SparkMaxAbs:10", offset = 0.25 } }

[[part]]
name = "fr"
stock = "swerve/MAXSwerve"
ratio = "14T"
attach = "drive.front_right"
ids = { drive = "SparkMax:15", steer = "SparkMax:14", encoder = "SparkMaxAbs:14" }

[[part]]
name = "bl"
stock = "swerve/MAXSwerve"
ratio = "14T"
attach = "drive.back_left"
ids = { drive = "SparkMax:13", steer = "SparkMax:12", encoder = { rev = "SparkMaxAbs:12", offset = -0.5 } }

[[part]]
name = "br"
stock = "swerve/MAXSwerve"
ratio = "14T"
attach = "drive.back_right"
ids = { drive = "SparkMax:17", steer = "SparkMax:16", encoder = { rev = "SparkMaxAbs:16", offset = -0.25 } }
"""

_DIFFERENTIAL = """\
[[part]]
name = "drive"
stock = "chassis"
ids = { gyro = { device = "ADXRS450[0]", axis = "x", cw_positive = true } }

[[part]]
name = "dt"
stock = "differential"    # 3 wheels per side, 6 in, the middle ones 3 mm lower (drop center)
attach = "drive.center"
ids = { left = "PWM:0", right = "PWM:1", left_encoder = { channel_a = 0, counts_per_rev = 2048 }, right_encoder = { channel_a = 2, counts_per_rev = 2048 } }
"""

_STANDIN = _DIFFERENTIAL.replace(
    'stock = "chassis"\n',
    'stock = "chassis"\n'
    '# Attach your part to drive.top, or to wherever its CAD has it. Set mass to what the rest of the robot weighs and\n'
    '# com_offset to move the robot\'s center of mass [forward, left, up] in meters.\n'
    'mass = 45.0\n'
    'com_offset = [0.0, 0.0, 0.0]\n', 1)

KINDS = ("swerve", "swerve-rev", "differential", "standin")


def new_config(kind: str) -> str:
    if kind == "swerve":
        return _HEADER.format(title="A swerve robot: a chassis and four SDS MK4i modules.") + _SWERVE
    if kind == "swerve-rev":
        return _HEADER.format(title="A REV swerve robot: a chassis and four MAXSwerve modules on SPARK MAX controllers.") + _SWERVE_REV
    if kind == "differential":
        return _HEADER.format(title="A differential-drive robot: a chassis and a 6-wheel drop-center drivetrain.") + _DIFFERENTIAL
    if kind == "standin":
        return _HEADER.format(title="Stand-in box robot: the kitbot's chassis and drive, for trying a part on its own.") + _STANDIN
    raise ValueError(f"unknown template {kind!r} (use one of: {', '.join(KINDS)})")


_SCENARIO = """\
# A scenario: the robot program runs through these steps in the simulator; checks pass or fail.
# Edit it with `scrimmage edit {{this file}}`, or by hand. Run it with `scrimmage run {{this file}}`.
config = {config}

[[step]]
name = "idle"
mode = "disabled"
duration = 1.0

[[step]]
name = "auto"
mode = "auto"
duration = 5.0
  # A check: uncomment and change the text to a line your robot program prints.
  # [[step.check]]
  # console = {{ contains = "Auto selected" }}
"""


def new_scenario(config_rel_path: str) -> str:
    """A starter scenario for the robot config at `config_rel_path` (relative to the scenario's folder): a 1 s
    disabled step, then a 5 s auto step with a placeholder console check commented out."""
    return _SCENARIO.format(config=json.dumps(str(config_rel_path)))
