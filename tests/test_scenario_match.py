"""A scenario's [match] and each step's game_data: loading them, and what the runner sends the robot (spec M4)."""

import asyncio
import json
from pathlib import Path

import pytest

from scrimmage.driverstation import DriverStationState
from scrimmage.scenario import ScenarioError, load_scenario, load_scenario_text
from scrimmage.scenario_runner import run_scenario
from scrimmage.session import Session

TESTS = Path(__file__).resolve().parent
FAKE = TESTS / "fake_robot.toml"

HUB = """
[match]
station = "red1"

[[step]]
name = "auto"
mode = "auto"
duration = 0.3

[[step]]
name = "shift"
mode = "teleop"
duration = 0.3
game_data = "B"

[[step]]
name = "later"
mode = "teleop"
duration = 0.3
"""


def write(tmp_path: Path, body: str, name: str = "s.toml") -> Path:
    p = tmp_path / name
    p.write_text(f'config = "{FAKE}"\n' + body)
    return p


def load(body: str):
    return load_scenario_text(f'config = "{FAKE}"\n' + body, TESTS / "x.toml")


def test_match_and_game_data_load():
    scn = load(HUB)
    assert scn.match == {"station": "red1"}
    assert [s.game_data for s in scn.steps] == [None, "B", None]
    assert load('[[step]]\nmode = "auto"\nduration = 1\n').match == {}


@pytest.mark.parametrize("body, message", [
    ('[match]\nstation = "red4"\n', r"x\.toml: \[match\] station must be one of red1, red2, red3, blue1, blue2, "
                                    r"blue3, not 'red4'"),
    ('[match]\nalliance = "red"\n', r"x\.toml: \[match\] has no key 'alliance'"),
    ('[match]\ntimer = "yes"\n', r"x\.toml: \[match\] timer must be true or false"),
    ('[match]\nauto_seconds = 0\n', r"x\.toml: \[match\] auto_seconds must be a positive number of seconds"),
    ('match = "red1"\n', r"x\.toml: \[match\] must be a table"),
])
def test_a_bad_match_is_an_error(body, message):
    with pytest.raises(ScenarioError, match=message):
        load(body + '[[step]]\nmode = "auto"\nduration = 1\n')


@pytest.mark.parametrize("value, message", [
    ('"' + "x" * 65 + '"', r"x\.toml, step 2 \(shift\): game_data must be at most 64 bytes"),
    ("3", r"x\.toml, step 2 \(shift\): game_data must be text, not 3"),
])
def test_bad_game_data_is_an_error_naming_the_step(value, message):
    with pytest.raises(ScenarioError, match=message):
        load(f'[[step]]\nmode = "auto"\nduration = 1\n[[step]]\nname = "shift"\nmode = "teleop"\nduration = 1\n'
             f'game_data = {value}\n')


@pytest.fixture
def sent(monkeypatch):
    """Every DriverStation frame the bridge sends, as (sim time, data)."""
    out = []
    orig = DriverStationState.frames

    def frames(self, now):
        f = orig(self, now)
        out.append((now, json.loads(f[-1])["data"]))
        return f
    monkeypatch.setattr(DriverStationState, "frames", frames)
    return out


def during(sent, step: dict) -> list[dict]:
    """The frames sent inside a step (its report's start_t to end_t, ends left out: a frame there may be either
    step's)."""
    got = [d for now, d in sent if step["start_t"] < now < step["end_t"]]
    assert got, f"no frames during {step['name']}"
    return got


def test_the_robot_gets_the_station_and_each_steps_game_data(tmp_path, sent):
    rep = asyncio.run(run_scenario(load_scenario(write(tmp_path, HUB)), tmp_path / "out"))
    assert rep.error is None, rep.error
    steps = rep.to_dict()["steps"]
    assert [s["game_data"] for s in steps] == ["", "B", "B"]
    assert all(d[">station"] == "red1" for _, d in sent)          # from the robot's first frame
    assert all(d.get(">game_data", "") == "" for d in during(sent, steps[0]))
    assert all(d[">game_data"] == "B" for d in during(sent, steps[1]) + during(sent, steps[2]))
    md = (tmp_path / "out" / "report.md").read_text()
    assert "## auto (auto, t=" in md and "game data" not in md.split("## shift")[0]
    assert '## shift (teleop, t=0.30–0.60 s, game data "B")' in md


def test_back_to_back_scenarios_each_start_with_their_own_match(tmp_path, sent):
    """Review Focus 2: a second scenario on the same session starts with no game data and its own station."""
    first = load_scenario(write(tmp_path, HUB, "first.toml"))
    second = load_scenario(write(tmp_path, '[match]\nstation = "blue2"\n\n[[step]]\nname = "teleop"\n'
                                           'mode = "teleop"\nduration = 0.3\n', "second.toml"))
    third = load_scenario(write(tmp_path, '[[step]]\nname = "teleop"\nmode = "teleop"\nduration = 0.3\n',
                                "third.toml"))

    async def main():
        s = Session(FAKE, nt=False)
        try:
            await s.start()
            rep = await run_scenario(first, tmp_path / "out1", session=s)
            assert rep.error is None, rep.error
            assert s.bridge.ds.game_data == "B"
            sent.clear()
            rep2 = await run_scenario(second, tmp_path / "out2", session=s)
            assert rep2.error is None, rep2.error
            got = list(sent)
            sent.clear()
            rep3 = await run_scenario(third, tmp_path / "out3", session=s)
            assert rep3.error is None, rep3.error
            return rep2, got, rep3, list(sent)
        finally:
            await s.stop()
    rep2, got2, rep3, got3 = asyncio.run(main())
    assert got2 and all(d[">station"] == "blue2" and d[">game_data"] == "" for _, d in got2)
    assert rep2.to_dict()["steps"][0]["game_data"] == ""
    assert got3 and all(d[">station"] == "blue1" for _, d in got3)    # no [match]: the config's
