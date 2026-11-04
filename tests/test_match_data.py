"""Match data sent as the Driver Station: the station, FMS attached, game data and the match timer (spec M1-M6)."""

import asyncio
import json
from pathlib import Path

import pytest

from scrimmage.config import ConfigError, load_config_text
from scrimmage.driverstation import GAME_DATA_MAX, DriverStationState, MatchSettings, check_game_data
from scrimmage.session import Session, SessionError

TESTS = Path(__file__).resolve().parent
FAKE = TESTS / "fake_robot.toml"


def ds_data(ds: DriverStationState, now: float) -> dict:
    return json.loads(ds.frames(now)[-1])["data"]


def match_time(ds: DriverStationState, now: float) -> float:
    return ds_data(ds, now)[">match_time"]


def test_default_frames_are_unchanged():
    ds = DriverStationState(active=True)
    assert ds.frames(0.0)[-1] == ('{"type":"DriverStation","device":"","data":{">enabled":false,">autonomous":false,'
                                  '">test":false,">ds":true,">station":"blue1",">match_time":-1.0,">new_data":true}}')
    ds.set_mode("teleop")
    assert ds_data(ds, 5.0) == {">enabled": True, ">autonomous": False, ">test": False, ">ds": True,
                                ">station": "blue1", ">match_time": -1.0, ">new_data": True}


def test_game_data_is_sent_every_frame_once_set():
    ds = DriverStationState(active=True)
    ds.changed = False
    ds.set_game_data("R")
    assert ds.changed
    for now in (0.0, 0.02, 0.04):
        d = ds_data(ds, now)
        assert d[">game_data"] == "R" and d[">fms"] is False
    ds.set_game_data("")
    assert ds_data(ds, 0.06)[">game_data"] == ""        # cleared, and still sent so the robot hears it


def test_station_and_fms_are_sent():
    ds = DriverStationState(active=True)
    ds.changed = False
    ds.set_match(station="red2", fms=True)
    assert ds.changed
    d = ds_data(ds, 0.0)
    assert d[">station"] == "red2" and d[">fms"] is True and d[">game_data"] == ""
    ds.set_match(fms=False)
    assert ds_data(ds, 0.02)[">fms"] is False           # once in, the key stays


def test_the_timer_counts_down_holds_and_restarts():
    ds = DriverStationState(active=True)
    ds.set_match(timer=True)
    assert match_time(ds, 0.0) == -1                    # before any phase
    ds.set_mode("auto")
    assert match_time(ds, 1.0) == 20.0
    assert match_time(ds, 6.0) == 15.0
    ds.set_mode("disabled")
    assert match_time(ds, 8.0) == 13.0
    assert match_time(ds, 9.0) == 13.0                  # holds while disabled
    ds.set_mode("teleop")
    assert match_time(ds, 10.0) == 140.0
    assert match_time(ds, 10.5) == 139.5
    assert match_time(ds, 151.0) == 0
    assert match_time(ds, 200.0) == 0                   # never negative
    ds.set_mode("test")
    assert match_time(ds, 201.0) == -1
    ds.set_mode("auto")                                 # a second Auto starts again at its full length
    assert match_time(ds, 202.0) == 20.0


def test_auto_again_after_a_disable_between_frames_restarts():
    ds = DriverStationState(active=True)
    ds.set_match(timer=True)
    ds.set_mode("auto")
    assert match_time(ds, 0.0) == 20.0
    ds.set_mode("disabled")
    ds.set_mode("auto")                                 # no frame saw the disable
    assert match_time(ds, 5.0) == 20.0


def test_rounded_to_a_millisecond():
    ds = DriverStationState(active=True)
    ds.set_match(timer=True)
    ds.set_mode("auto")
    ds.frames(0.0)
    assert match_time(ds, 0.1 + 0.2) == 19.7


def test_with_the_timer_off_it_is_always_minus_one():
    ds = DriverStationState(active=True)
    for mode, now in (("auto", 0.0), ("auto", 5.0), ("disabled", 6.0), ("teleop", 7.0), ("teleop", 300.0),
                      ("test", 301.0)):
        ds.set_mode(mode)
        assert match_time(ds, now) == -1


def test_turning_the_timer_on_part_way_through_teleop_starts_at_the_full_length():
    ds = DriverStationState(active=True)
    ds.set_mode("teleop")
    assert match_time(ds, 0.0) == -1
    assert match_time(ds, 30.0) == -1
    ds.set_match(timer=True)
    assert match_time(ds, 31.0) == 140.0
    assert match_time(ds, 41.0) == 130.0


def test_a_changed_length_takes_effect_at_the_next_entry():
    ds = DriverStationState(active=True)
    ds.set_match(timer=True, auto_seconds=15)
    ds.set_mode("auto")
    assert match_time(ds, 0.0) == 15.0
    ds.set_match(auto_seconds=10)
    assert match_time(ds, 1.0) == 14.0
    ds.set_mode("disabled")
    ds.frames(2.0)
    ds.set_mode("auto")
    assert match_time(ds, 3.0) == 10.0


def test_clear_keeps_the_settings_and_drops_the_game_data_and_the_time():
    ds = DriverStationState(active=True)
    ds.set_match(station="red3", timer=True)
    ds.set_game_data("B")
    ds.set_mode("teleop")
    assert match_time(ds, 0.0) == 140.0
    ds.clear()
    assert ds.mode == "disabled" and ds.game_data == ""
    assert ds.match.station == "red3" and ds.match.timer is True
    d = ds_data(ds, 1.0)
    assert d[">game_data"] == "" and d[">match_time"] == -1 and d[">station"] == "red3"


@pytest.mark.parametrize("table, key", [
    ({"station": "red4"}, "station"),
    ({"auto_seconds": 0}, "auto_seconds"),
    ({"teleop_seconds": -5}, "teleop_seconds"),
    ({"timer": "yes"}, "timer"),
    ({"fms": 1}, "fms"),
    ({"game_data": "R"}, "game_data"),
    ({"stations": "red1"}, "stations"),
])
def test_merged_rejects_bad_values_naming_the_key(table, key):
    with pytest.raises(ValueError, match=rf"\[match\] .*{key}"):
        MatchSettings().merged(table, "[match]")


def test_merged_names_the_allowed_values():
    with pytest.raises(ValueError, match="red1, red2, red3, blue1, blue2, blue3"):
        MatchSettings().merged({"station": "red4"}, "[match]")
    with pytest.raises(ValueError, match="station, fms, timer, auto_seconds, teleop_seconds"):
        MatchSettings().merged({"colour": "red"}, "[match]")


def test_merged_applies_keys_over_the_values():
    base = MatchSettings().merged({"station": "red1", "timer": True}, "config")
    m = base.merged({"station": "blue3", "auto_seconds": 15}, "scenario")
    assert m == MatchSettings(station="blue3", fms=False, timer=True, auto_seconds=15.0, teleop_seconds=140.0)
    assert base.station == "red1"                       # a copy


def test_game_data_is_at_most_64_characters():
    assert check_game_data("x" * GAME_DATA_MAX, "here") == "x" * 64
    with pytest.raises(ValueError, match="here.*64 characters"):
        check_game_data("x" * 65, "here")
    with pytest.raises(ValueError, match="here"):
        check_game_data(3, "here")
    ds = DriverStationState()
    with pytest.raises(ValueError, match="64"):
        ds.set_game_data("x" * 65)


def test_set_match_rejects_bad_values_and_changes_nothing():
    ds = DriverStationState()
    with pytest.raises(ValueError, match="station"):
        ds.set_match(station="red4", fms=True)
    assert ds.match == MatchSettings()


def test_a_config_match_loads():
    cfg = load_config_text('[match]\nstation = "red2"\nfms = true\ntimer = true\nteleop_seconds = 135\n',
                           TESTS / "x.toml")
    assert cfg.match == MatchSettings(station="red2", fms=True, timer=True, teleop_seconds=135.0)
    assert load_config_text("", TESTS / "x.toml").match == MatchSettings()


@pytest.mark.parametrize("text", ['[match]\nstation = "red4"\n', "[match]\nauto_seconds = 0\n",
                                  '[match]\ncolor = "red"\n', 'match = "red1"\n'])
def test_a_bad_config_match_raises(text):
    with pytest.raises(ConfigError, match=r"\[match\]"):
        load_config_text(text, TESTS / "x.toml")


def test_session_set_match_changes_the_state(tmp_path):
    cfg = tmp_path / "robot.toml"
    text = FAKE.read_text().replace('"models/', f'"{TESTS / "models"}/').replace('cwd = "."', f'cwd = "{TESTS}"')
    cfg.write_text(text + '\n[match]\nstation = "red2"\n')

    async def main():
        s = Session(cfg, nt=False)
        try:
            await s.start()
            assert s.state()["match"] == {"station": "red2", "fms": False, "timer": False, "auto_seconds": 20.0,
                                          "teleop_seconds": 140.0, "game_data": "", "match_time": -1.0}
            st = s.set_match(station="blue3", fms=True, timer=True, game_data="R", teleop_seconds=100)
            assert st["match"] == {"station": "blue3", "fms": True, "timer": True, "auto_seconds": 20.0,
                                   "teleop_seconds": 100.0, "game_data": "R", "match_time": -1.0}
            s.set_mode("teleop")
            st = await s.advance(1.0)
            assert 98.9 < st["match"]["match_time"] < 99.1      # as last sent
            with pytest.raises(SessionError, match="station"):
                s.set_match(station="red4", game_data="B")
            with pytest.raises(SessionError, match="64"):
                s.set_match(station="red1", game_data="x" * 65)
            assert s.state()["match"]["station"] == "blue3" and s.state()["match"]["game_data"] == "R"
        finally:
            await s.stop()
    asyncio.run(main())
