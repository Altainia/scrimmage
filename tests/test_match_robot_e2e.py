"""Match data read by a real RobotPy program in a held session: tests/match_probe_py prints the game data, the
alliance, the station number, FMS attached and the match time that scrimmage, as the Driver Station, sends through
halsim_ws (spec M7)."""

import asyncio
import re
from pathlib import Path

import pytest

pytest.importorskip("wpilib")
pytest.importorskip("halsim_ws")
pytest.importorskip("scrimmage_lockstep")

from scrimmage.session import Session  # noqa: E402

TESTS = Path(__file__).resolve().parent
PROBE = TESTS / "match_probe_py"
PRINT = re.compile(r"match data='([^']*)' alliance=(\w+) location=(\w+) fms=(\w+) time=(-?[\d.]+)")

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

[telemetry]
nt = false

[[part]]
name = "drive"
stock = "chassis"
length = 0.66
width = 0.6
mass = 47.0
frame_height = 0.1

[match]
station = "red2"
fms = true
timer = true
"""


def prints(s: Session, since: int) -> list[tuple]:
    return PRINT.findall("\n".join(line["text"] for line in s.console(since)["lines"]))


@pytest.fixture(scope="module")
def run(tmp_path_factory) -> dict:
    """Teleop, game data "R", 3 s; then a restarted robot program, teleop again and 1 s."""
    cfg = tmp_path_factory.mktemp("match_py") / "match_probe_py.toml"
    cfg.write_text(CONFIG)

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            s.set_mode("teleop")
            s.set_match(game_data="R")
            await s.advance(3.0)
            first = prints(s, 0)
            hal = s.hal("DriverStation")
            await s.restart_robot()
            since = s.console()["next"]
            s.set_mode("teleop")
            await s.advance(1.0)
            return {"first": first, "after_restart": prints(s, since), "hal": hal,
                    "console": "\n".join(line["text"] for line in s.console(0)["lines"])}
        finally:
            await s.stop()
    return asyncio.run(main())


def test_the_robot_reads_the_match_data(run):
    assert len(run["first"]) >= 3, (run["console"][-2000:], run["hal"])
    for data, alliance, location, fms, time in run["first"]:
        assert (data, alliance, location, fms) == ("R", "kRed", "2", "True"), (run["first"], run["hal"])
    times = [float(p[4]) for p in run["first"]]
    assert 130 < times[-1] < 139 and times == sorted(times, reverse=True), times


def test_a_restarted_robot_program_gets_the_game_data_back(run):
    assert run["after_restart"], run["console"][-2000:]
    assert all(p[:4] == ("R", "kRed", "2", "True") for p in run["after_restart"]), run["after_restart"]
