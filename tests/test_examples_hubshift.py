"""The hub-shift example (examples/robotpy_hubshift): a RobotPy robot whose is_hub_active() follows WPILib's 2026
example, run through a whole match by hubshift.scenario.toml. Each test asserts what the example's README says: the
run passes, the hub changes exactly at the shift boundaries (well inside each 0.2 s step, spec M6), and as blue1 the
four checks at the starts of the shifts fail."""

import asyncio
import re
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("wpilib")
pytest.importorskip("halsim_ws")
pytest.importorskip("scrimmage_lockstep")

from scrimmage.scenario import load_scenario  # noqa: E402
from scrimmage.scenario_runner import run_scenario  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "robotpy_hubshift"
SCENARIO = EXAMPLE / "hubshift.scenario.toml"
LINE = re.compile(r"^\[\s*([\d.]+)\] hub (active|inactive) \(match time (-?[\d.]+) s\)$", re.M)
STEPS = ["before the match", "auto", "the pause", "transition", "shift 1 starts", "shift 1", "shift 2 starts",
         "shift 2", "shift 3 starts", "shift 3", "shift 4 starts", "shift 4", "end game"]
STARTS = ["shift 1 starts", "shift 2 starts", "shift 3 starts", "shift 4 starts"]


def _run(path: Path, out: Path) -> dict:
    rep = asyncio.run(run_scenario(load_scenario(path), out))
    flips = [(float(t), state, float(time)) for t, state, time in LINE.findall((out / "robot.log").read_text())]
    return {"rep": rep, "steps": rep.to_dict()["steps"], "flips": flips}


@pytest.fixture(scope="module")
def red1(tmp_path_factory):
    return _run(SCENARIO, tmp_path_factory.mktemp("hubshift"))


def test_the_example_scenario_passes(red1):
    rep, steps = red1["rep"], red1["steps"]
    assert rep.error is None and rep.passed, rep.to_dict()
    assert [s["name"] for s in steps] == STEPS
    assert all(s["passed"] for s in steps)
    assert [s["game_data"] for s in steps] == ["", "", ""] + ["R"] * 10
    raw = tomllib.loads(SCENARIO.read_text(encoding="utf-8"))
    assert raw["match"] == {"station": "red1", "timer": True}
    assert sum(s["duration"] for s in raw["step"] if s["mode"] == "teleop") == pytest.approx(140.0)


def test_the_hub_changes_at_each_shift(red1):
    """The robot's printed answers, in order: inactive before the match, active in Auto, inactive in the pause, then
    in Teleop active (transition), inactive at 130 s, active at 105 s, inactive at 80 s, active at 55 s, and no change
    at 30 s (the End Game is active, like Shift 4)."""
    flips = red1["flips"]
    assert [s for _, s, _ in flips] == ["inactive", "active", "inactive", "active", "inactive", "active", "inactive",
                                        "active"], flips
    assert [m for _, _, m in flips[1:]] == [20.0, 0.0, 140.0, 130.0, 105.0, 80.0, 55.0], flips
    # each Teleop change lands in its own 0.2 s step, at least 20 ms (one robot loop) from both of its ends (M6)
    by_name = {s["name"]: s for s in red1["steps"]}
    for (t, _, _), name in zip(flips[4:], STARTS):
        step = by_name[name]
        assert step["start_t"] + 0.02 <= t <= step["end_t"] - 0.02, (name, t, step["start_t"], step["end_t"])


def test_as_blue1_the_shift_starts_fail(tmp_path):
    """The README's change: the same match as blue1. Blue's hub is active first after red's "R", so it changes at
    the same times the other way round, and the four checks at the shift starts fail."""
    text = SCENARIO.read_text(encoding="utf-8")
    assert text.count('station = "red1"') == 1 and text.count('config = "hubshift.toml"') == 1
    text = text.replace('station = "red1"', 'station = "blue1"')
    text = text.replace('config = "hubshift.toml"', f'config = "{EXAMPLE / "hubshift.toml"}"')
    path = tmp_path / "blue1.scenario.toml"
    path.write_text(text, encoding="utf-8")
    run = _run(path, tmp_path / "out")
    assert run["rep"].error is None and not run["rep"].passed
    assert [s["name"] for s in run["steps"] if not s["passed"]] == STARTS
    assert [s for _, s, _ in run["flips"][3:]] == ["active", "inactive", "active", "inactive", "active"]
    assert [m for _, _, m in run["flips"][3:]] == [140.0, 105.0, 80.0, 55.0, 30.0]


def test_the_readme_shows_the_runs_console(red1):
    readme = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    shown = re.findall(r"^hub (active|inactive) \(match time (-?[\d.]+) s\)", readme, re.M)
    assert [(s, float(m)) for s, m in shown] == [(s, m) for _, s, m in red1["flips"]]
    assert "follows WPILib's example" in readme
    for call in ("getGameSpecificMessage()", "getMatchTime()", "getAlliance()"):
        assert call in readme and call in (EXAMPLE / "robot.py").read_text(encoding="utf-8")
