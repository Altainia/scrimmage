"""A grabber for tests of pieces beyond balls: a hand-written part on the stand-in robot (an arm on a hinge carrying
two jaws on slides, each with its motor), on a range of its own with one coral lying across in front of it, between
the open jaws. `grab_test` writes the folder and a test file; the `grab_run` fixture runs the closed grabber swinging
the coral up (import it into a test module to use it)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scrimmage.blocks.program import load
from scrimmage.blocks.run import TestRun, world_for
from scrimmage.blocks.spread import placements, spread_ball

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "blocks" / "intake"
CORAL_X = 0.15          # m: the coral's center forward of the bumper's face, between the jaws

# Coordinates as the intake's: x forward, y left, z up, in meters; the origin on the floor at the bumper's face. The
# arm turns about y at a pivot 30 cm up; a negative angle swings the jaws forward and up. Each jaw is a pad on a
# slide toward the coral (positive volts close it), sprung open; a 4.5 in coral lying across fits between them with
# 3 cm to spare each side. The arm is geared 100:1, each jaw 300 rad of motor per meter (a NEO squeezes about 13 N
# at 0.2 V, 260 N at 4 V); each joint's armature is a motor rotor (1e-4 kg m^2, the stock swerve's ROTOR_MOI) seen
# through that gearing. The pads are plain boxes with MuJoCo's default contact (the coral's own firm contact governs,
# testrange/build.py's RIGID_SOLREF). Every number is our choice.
GRABBER_PART = """
[part]
name = "grabber"
format = 1

[[body]]
name = "base"
mass = 0.5
geom = [{ box = [0.04, 0.04, 0.30], pos = [0.0, 0.0, 0.15], color = [0.55, 0.55, 0.6, 1.0], collide = false }]

[[body]]
name = "arm"
parent = "base"
pos = [0.0, 0.0, 0.30]
mass = 1.0
joint = { name = "arm", hinge = "y", range = [-120, 10], armature = 1.0 }
geom = [{ box = [0.02, 0.24, 0.02], pos = [0.15, 0.0, -0.13], color = [0.6, 0.6, 0.65, 1.0], collide = false }]

[[body]]
name = "jaw_b"
parent = "arm"
pos = [0.0528, 0.0, -0.24]
mass = 0.2
joint = { name = "jaw_b", slide = "x", range = [0.0, 0.05], armature = 9.0, spring = { stiffness = 50.0 } }
geom = [{ box = [0.02, 0.20, 0.08], color = [0.2, 0.2, 0.2, 1.0] }]

[[body]]
name = "jaw_f"
parent = "arm"
pos = [0.2472, 0.0, -0.24]
mass = 0.2
joint = { name = "jaw_f", slide = [-1, 0, 0], range = [0.0, 0.05], armature = 9.0, spring = { stiffness = 50.0 } }
geom = [{ box = [0.02, 0.20, 0.08], color = [0.2, 0.2, 0.2, 1.0] }]

[[motor]]
slot = "arm"
joint = "arm"
motor = "NEO"
gearing = 100.0

[[motor]]
slot = "jaw_b"
joint = "jaw_b"
motor = "NEO"
gearing = 300.0

[[motor]]
slot = "jaw_f"
joint = "jaw_f"
motor = "NEO"
gearing = 300.0

[[mount]]
name = "base"
body = "base"
"""

GRAB_RANGE = f"""
[range]
name = "A coral to grab"

[piece.coral]
preset = "2025-coral"

[[floor_balls]]
at = [[{2.0 + 0.43 + CORAL_X:.4f}, 4.0]]
kind = "coral"
tip_deg = 0
rotation_deg = 90

[start.facing_coral]
pose = [2.0, 4.0, 0.0]
"""


def squeeze(volts: float) -> str:
    """Blocks spinning both jaws at `volts` (positive closes them)."""
    return "".join(f"""
[[block]]
do = "spin"
motor = "grabber.{jaw}"
volts = {volts}
""" for jaw in ("jaw_b", "jaw_f"))


def wait(seconds: float) -> str:
    return f"""
[[block]]
do = "wait"
seconds = {seconds}
"""


def move_arm(deg: float) -> str:
    return f"""
[[block]]
do = "move"
joint = "grabber.arm"
to = {deg}
unit = "deg"
"""


EXPECT_HELD = """
[[block]]
do = "expect"
that = { held = { piece = "any", by = "grabber" } }
throughout = true
"""


def blocks(jaw_volts: float = 4.0, throughout: bool = True, after: str = "") -> str:
    """The grabber's blocks: close the jaws firmly (4 V), lift the coral off the carpet (15 degrees), set the squeeze
    to `jaw_volts` and wait more than SLIP_LOOKBACK_S, then (`throughout`) expect it held throughout while the arm
    swings up to 80 degrees and stays there half a second; `after` is TOML blocks to play last. Blocks 1-2 close the
    jaws, 4 lifts, 6-7 set the squeeze, 9 is the expect."""
    return (squeeze(4.0) + wait(1.0) + move_arm(-15) + wait(0.3) + squeeze(jaw_volts) + wait(1.2)
            + (EXPECT_HELD if throughout else "") + move_arm(-80) + wait(0.5) + after)


def grab_test(folder: Path, text: str | None = None, hopper: list[str] | None = None) -> Path:
    """The stand-in with the grabber on the grab range in `folder`, and a test file playing `text` (TOML blocks;
    default `blocks()`) with `hopper` (where a piece should end up; None: none named); the test file's path."""
    folder.mkdir(parents=True, exist_ok=True)
    part = folder / "parts" / "grabber"
    part.mkdir(parents=True, exist_ok=True)
    (part / "part.toml").write_text(GRABBER_PART, encoding="utf-8")
    (folder / "grab.toml").write_text(GRAB_RANGE, encoding="utf-8")
    robot = (EXAMPLE / "intake-standin.toml").read_text(encoding="utf-8")
    robot = robot.replace('name = "intake"\npath = "parts/intake"', 'name = "grabber"\npath = "parts/grabber"')
    robot = robot.replace('ids = { roller = "PWM:2" }', 'ids = { arm = "PWM:2", jaw_b = "PWM:3", jaw_f = "PWM:4" }')
    robot = robot.replace('file = "intake"\nstart = "facing_ball"', 'file = "grab.toml"\nstart = "facing_coral"')
    (folder / "grab-standin.toml").write_text(robot, encoding="utf-8")
    path = folder / "grab.test.toml"
    head = '[test]\nrobot = "grab-standin.toml"\nchart = ["grabber.arm"]\n'
    if hopper is not None:
        head += f"hopper = {json.dumps(hopper)}\n"
    path.write_text(head + (blocks() if text is None else text), encoding="utf-8")
    return path


def run_grab(folder: Path, text: str | None = None, hopper: list[str] | None = None, floor: bool = False):
    """grab_test's run: (program, world, result). With `floor`, the coral is tracked as a floor spread's piece is
    (it stays where the range puts it), so the run's feeds give its outcome."""
    program = load(grab_test(folder, text, hopper))
    world = world_for(program)
    placement = None
    if floor:
        placement = placements({}, spread_ball(world)[1])[0]
    return program, world, TestRun(world, program, placement=placement).run()


@pytest.fixture(scope="module")
def grab_run(tmp_path_factory):
    """The closed grabber (4 V on each jaw) swinging the coral up, expecting it held throughout: the RunResult."""
    return run_grab(tmp_path_factory.mktemp("grab"))[2]

