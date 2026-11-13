"""CANrange and LaserCAN [[sensor]]s read by a real RobotPy program under scrimmage: tests/distance_probe_py (phoenix6's
CANrange, libgrapplefrc-py's LaserCAN through scrimmage's grapple helper). The RobotPy twin of
tests/test_java_e2e.py's test_java_reads_can_distance_sensors, with the same world and numbers.

A RobotPy program with Phoenix 6 devices runs in real time: Phoenix's status signals refresh on the wall clock, so the
test waits for the values it expects, never for a time."""

import asyncio
import math
import re
from pathlib import Path

import pytest

pytest.importorskip("wpilib")
pytest.importorskip("halsim_ws")
pytest.importorskip("phoenix6")
pytest.importorskip("libgrapplefrc")
pytest.importorskip("scrimmage_lockstep")

from scrimmage.session import Session  # noqa: E402

TESTS = Path(__file__).resolve().parent
PROBE = TESTS / "distance_probe_py"

RANGE_LINE = re.compile(r"range cr5=([-\d.]+),(\w+) cr8=([-\d.]+),(\w+) lc7=(\S+) lc9=(\S+) loop=(\d+)")
CARGO_R = 0.12065           # m: a 2022 cargo's radius
LANES = {"cr5": 0.45, "lc7": 0.15, "cr8": -0.15, "lc9": -0.45}   # each sensor's y on the chassis: 0.3 m apart
SENSOR_X = 0.41             # m ahead of the chassis's center (its front bumper face)
# Phoenix 6 keeps a CANrange's distance in whole millimeters, rounded down, so the robot can read up to 1 mm short of
# scrimmage (as in the Java test).
CANRANGE_TOL = 0.0011

# The probe's devices on a kitbot built from parts, looking ahead at a cargo's center height, each down its own lane.
# The range's four cargo start far off to the side; the test moves them.
RANGE_CONFIG = """
[model]
timestep = 0.001
[lockstep]
step = 0.005
[robot]
start_pose = [3.0, 4.0, 0.0]
[robot_program]
command = ["{{python}}", "-m", "robotpy", "sim", "--nogui", "--ws-client", "--scrimmage-lockstep"]
cwd = "{probe}"
[range]
file = "four_cargo.toml"
[[part]]
name = "drive"
stock = "chassis"
length = 0.66
width = 0.6
mass = 47.0
frame_height = 0.1
""" + "".join(f"""
[[sensor]]
name = "{name}"
kind = "distance"
body = "drive.chassis"
at = [{SENSOR_X}, {y}, {CARGO_R}]
toward = [1, 0, 0]
{"ctre" if name.startswith("cr") else "grapple"} = "{"CANrange" if name.startswith("cr") else "LaserCAN"}:{name[2:]}"
""" for name, y in LANES.items())
FOUR_CARGO = """
[range]
name = "Four cargo"
[piece.cargo]
ball = "2022-cargo"
color = "red"
[[floor_balls]]
at = [[6.0, 1.0], [6.5, 1.0], [7.0, 1.0], [7.5, 1.0]]
kind = "cargo"
"""


async def range_print(s: Session, ok, what: str, timeout: float = 20.0) -> tuple:
    """The probe's first "range" print, after this call, for which ok(print) holds (wait on the values, not a time)."""
    start = len(s.console()["lines"])
    got = None
    text = ""
    for _ in range(int(timeout / 0.1)):
        text = "\n".join(line["text"] for line in s.console(start)["lines"])
        for m in RANGE_LINE.finditer(text):
            got = m.groups()
            if ok(got):
                return got
        await s.advance(0.1)
    raise AssertionError(f"no range print with {what} within {timeout} s; last: {got}\n{text[-2000:]}")


def lasercan(field: str) -> tuple[int, int, str] | None:
    if field == "null":
        return None
    status, mm, mode = field.split(",")
    return int(status), int(mm), mode


def lasercan_is(field: str, status: int, mm: int, mode: str | None = None) -> bool:
    """Whether a printed LaserCAN reads `status` and `mm` (to 1 mm) and, if given, `mode`: a wait's predicate."""
    got = lasercan(field)
    return got is not None and got[0] == status and abs(got[1] - mm) <= 1 and mode in (None, got[2])


def test_robotpy_reads_can_distance_sensors(tmp_path):
    """Two CANranges (5, 8) and two LaserCANs (7, from a `from libgrapplefrc import LaserCAN` bound before scrimmage's
    extension loads; 9, through `libgrapplefrc.LaserCAN`) read scrimmage's physics, each its own cargo: CANrange 5
    0.25 m, LaserCAN 7 0.40 m, CANrange 8 0.60 m, LaserCAN 9 (long ranging) 2.0 m. Moved away: LaserCAN 7 no target,
    CANrange 5 not detected. Enabled, the probe sets LaserCAN 9 to short ranging: its cargo at 2.0 m, past short's
    1.3 m reach, reads no target."""
    from scrimmage.sensors.runtime import LASERCAN_STATUS_NO_TARGET, LASERCAN_STATUS_VALID
    from scrimmage.testrange.build import BALL_BITS
    (tmp_path / "four_cargo.toml").write_text(FOUR_CARGO)
    cfg = tmp_path / "range_probe_py.toml"
    cfg.write_text(RANGE_CONFIG.format(probe=PROBE))
    want = {"cr5": 0.25, "lc7": 0.40, "cr8": 0.60, "lc9": 2.0}   # m from each sensor to its cargo's near side

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            await s.advance(0.3)
            pose = s.state()["pose"]
            x0, y0, h = pose["x"], pose["y"], math.radians(pose["heading_deg"])
            c, si = math.cos(h), math.sin(h)
            balls = s.world.test_range.balls
            # The chassis body's origin rests a little off the carpet, so the lines pass that far off a resting
            # cargo's center: there the cargo's surface is `half` short of its center.
            off = s.world.data.xpos[s.world.robot_body][2]
            half = math.sqrt(CARGO_R ** 2 - off ** 2)

            def place(away: bool) -> None:
                """Each cargo down its sensor's line, its near side d away; or 2 m to the left."""
                for i, (name, d) in enumerate(want.items()):
                    ahead, left = SENSOR_X + d + half, LANES[name] + (2.0 if away else 0.0)
                    balls.put(i, [x0 + ahead * c - left * si, y0 + ahead * si + left * c, CARGO_R], BALL_BITS)

            place(away=False)
            await s.advance(0.1)
            seen = s.state()["sensors"]
            for name, d in want.items():   # scrimmage's own reading, before the robot's
                assert seen[name]["distance_m"] == pytest.approx(d, abs=0.001), (name, seen[name])

            def all_seen(p) -> bool:     # every value asserted below, so a print a step behind is waited past
                return (abs(float(p[0]) - 0.25) < CANRANGE_TOL and p[1] == "true"
                        and abs(float(p[2]) - 0.60) < CANRANGE_TOL
                        and lasercan_is(p[4], LASERCAN_STATUS_VALID, 400, "short")
                        and lasercan_is(p[5], LASERCAN_STATUS_VALID, 2000, "long"))
            p = await range_print(s, all_seen, "every device reading its cargo")
            text = "\n".join(line["text"] for line in s.console()["lines"])
            assert text.count("scrimmage grapple helper: loaded") == 1, text[-3000:]
            assert "unavailable" not in text and "cfg lasercan Long ok" in text, text[-3000:]
            assert float(p[0]) == pytest.approx(0.25, abs=CANRANGE_TOL) and p[1] == "true", p
            assert float(p[2]) == pytest.approx(0.60, abs=CANRANGE_TOL), p
            status, mm, mode = lasercan(p[4])
            assert status == LASERCAN_STATUS_VALID and mm == pytest.approx(400, abs=1) and mode == "short", p
            status, mm, mode = lasercan(p[5])
            assert status == LASERCAN_STATUS_VALID and mm == pytest.approx(2000, abs=1) and mode == "long", p

            place(away=True)
            p = await range_print(s, lambda p: (p[1] == "false" and abs(float(p[0]) - 3.0) < CANRANGE_TOL
                                                and lasercan(p[4]) == (LASERCAN_STATUS_NO_TARGET, 1300, "short")),
                                  "the cargo gone")
            assert lasercan(p[4]) == (LASERCAN_STATUS_NO_TARGET, 1300, "short"), p   # nothing: the short reach
            assert float(p[0]) == pytest.approx(3.0, abs=CANRANGE_TOL), p     # nothing: its max_range

            place(away=False)
            s.set_mode("teleop")
            p = await range_print(s, lambda p: (lasercan(p[5]) == (LASERCAN_STATUS_NO_TARGET, 1300, "short")
                                                and lasercan_is(p[4], LASERCAN_STATUS_VALID, 400)),
                                  "LaserCAN 9 in short ranging, past its reach")
            text = "\n".join(line["text"] for line in s.console()["lines"])
            assert "cfg lasercan Short ok" in text, text[-3000:]
            assert lasercan(p[5]) == (LASERCAN_STATUS_NO_TARGET, 1300, "short"), p
            assert s.state()["sensors"]["lc9"]["distance_m"] == pytest.approx(2.0, abs=0.001)   # still there
            status, mm, _ = lasercan(p[4])
            assert status == LASERCAN_STATUS_VALID and mm == pytest.approx(400, abs=1), p    # 7 is unchanged
        finally:
            await s.stop()
    asyncio.run(main())
