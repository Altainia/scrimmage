"""[[sensor]]s read by a real RobotPy program under scrimmage: tests/sensors_probe_py (a DigitalInput beam break, an
AnalogInput distance sensor and REVLib's ColorSensorV3 on the MXP port), through scrimmage's scenario runner, with a
red 2022 cargo resting in front of the robot.

halsim_ws_client relays the beam break and the distance sensor itself; REVLib's ColorSensorV3 creates the SimDevice
"REV Color Sensor V3[1,82]", which halsim_ws_client relays as type "SimDevice"."""

import asyncio
import re
from pathlib import Path

import pytest

pytest.importorskip("wpilib")
pytest.importorskip("halsim_ws")
pytest.importorskip("rev")
pytest.importorskip("scrimmage_lockstep")

from scrimmage.scenario import load_scenario_text  # noqa: E402
from scrimmage.scenario_runner import run_scenario  # noqa: E402

TESTS = Path(__file__).resolve().parent
PROBE = TESTS / "sensors_probe_py"
PRINT = re.compile(r"sensors beam=(\w+) volts=([\d.]+) raw=(\d+),(\d+),(\d+),(\d+) color=([\d.]+),([\d.]+),([\d.]+) "
                   r"prox=(\d+)")
CARGO_R = 0.12065           # m: a 2022 cargo's radius

# A red 2022 cargo on the carpet 0.6 m ahead of the robot's center (its near side 0.479 m ahead).
RANGE = """
[range]
name = "One red cargo"

[piece.red_cargo]
ball = "2022-cargo"
color = "red"

[[floor_balls]]
at = [[3.6, 4.0]]
kind = "red_cargo"
"""

# The kitbot at (3, 4) facing +x. In its chassis frame (origin on the floor at its center), at the cargo's center
# height: a beam across the cargo's center, a distance sensor at the robot's center looking ahead, and a color sensor
# 2.9 cm from the cargo.
CONFIG = f"""
[model]
timestep = 0.001

[lockstep]
step = 0.005

[robot]
start_pose = [3.0, 4.0, 0.0]

[robot_program]
command = ["{{python}}", "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]
cwd = "{PROBE}"

[range]
file = "one_red.toml"

[[part]]
name = "drive"
stock = "chassis"
length = 0.66
width = 0.6
mass = 47.0
frame_height = 0.1

[[sensor]]
name = "beam"
kind = "beam"
body = "drive.chassis"
from = [0.6, 0.3, {CARGO_R}]
to = [0.6, -0.3, {CARGO_R}]
dio = 0

[[sensor]]
name = "range"
kind = "distance"
body = "drive.chassis"
at = [0.0, 0.0, {CARGO_R}]
toward = [1, 0, 0]
analog = 0

[[sensor]]
name = "eye"
kind = "color"
body = "drive.chassis"
at = [0.45, 0.0, {CARGO_R}]
toward = [1, 0, 0]
i2c = "mxp"
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    """1 s disabled: the robot prints what it reads every 0.25 s."""
    tmp = tmp_path_factory.mktemp("sensors_py")
    (tmp / "one_red.toml").write_text(RANGE)
    cfg = tmp / "sensors_probe_py.toml"
    cfg.write_text(CONFIG)
    scenario = load_scenario_text(f'config = "{cfg}"\n[[step]]\nname = "wait"\nmode = "disabled"\nduration = 1.0\n',
                                  tmp / "probe_scenario.toml")
    out = tmp / "out"
    report = asyncio.run(run_scenario(scenario, out, watch=lambda s, t: None))
    console = (out / "robot.log").read_text()
    return {"report": report, "console": console, "prints": PRINT.findall(console)}


def test_the_run_passes(run):
    rep = run["report"]
    assert rep.error is None and rep.passed, rep.to_dict()
    assert not any("Color Sensor" in n for n in rep.notes), rep.notes
    assert len(run["prints"]) >= 3, run["console"][-2000:]


def test_the_robot_reads_the_beam_broken_and_the_distance_to_the_cargo(run):
    for beam, volts, *_ in run["prints"]:
        assert beam == "False", "active-low: the cargo on the beam reads False"
        assert float(volts) == pytest.approx((0.6 - CARGO_R) * 0.977, abs=0.003), volts


def test_the_robot_reads_the_red_cargo_on_the_color_sensor(run):
    for *_, red, green, blue, ir, r, g, b, prox in run["prints"]:
        red, green, blue, ir, prox = map(int, (red, green, blue, ir, prox))
        assert red > 1000 and red > 5 * blue and red > 5 * green, (red, green, blue)
        assert ir > 1000 and prox > 1000, (ir, prox)
        # getColor() is the raw color over its sum: red's (0.9, 0.1, 0.1) over 1.1
        assert (float(r), float(g), float(b)) == pytest.approx((0.9 / 1.1, 0.1 / 1.1, 0.1 / 1.1), abs=0.002)
