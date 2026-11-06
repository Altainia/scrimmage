"""Cached server text follows a live Language change (cleanup A8): why a scenario has no robot, why the Part tab's
part can't be edited, and the robot code panel's failed Build now result are worded again in the new language,
from what they were made of, without a new edit, build or file change."""
import json
import time

import pytest

from scrimmage import i18n
from test_assembler_server import Client
from test_part_server import _cache, _built, _plugin_folder, _state, _wait_for, part_app  # noqa: F401  (fixtures)

GB = {
    "_language": "English (UK)",
    "this scenario names no robot config ({setting})": "this scenario gives no robot config ({setting})",
    "the robot config {path} was deleted": "the robot config {path} has gone",
    "{name} was deleted; put it back, or open the CAD file again to make a new one":
        "{name} has gone; put it back, or open the CAD file again",
    "{why}; fix it in a text editor": "{why}; mend it in a text editor",
    "The robot code didn't build.": "The robot code wouldn't build.",
}


@pytest.fixture
def gb(tmp_path, monkeypatch):
    """GB as the en-GB catalog (the Language setting is left alone: the tests switch it)."""
    folder = tmp_path / "locales"
    folder.mkdir()
    (folder / "en-GB.json").write_text(json.dumps(GB, ensure_ascii=False), encoding="utf-8")
    shipped = i18n.LOCALES
    monkeypatch.setattr(i18n, "LOCALES", folder)
    i18n.reload()
    yield
    monkeypatch.setattr(i18n, "LOCALES", shipped)
    i18n.reload()


def test_why_a_scenario_has_no_robot_follows_the_language(gb, language, tmp_path):
    from scrimmage.assembler.server import App
    p = tmp_path / "s.toml"
    p.write_text('[[step]]\nmode = "auto"\nduration = 1.0\n')
    app = App(p, open_browser=False, runs_dir=tmp_path / "runs")
    try:
        c = Client(app, app.start())
        s = c.get("/api/robot/state")[1]
        assert s["read_only_reason"].startswith("this scenario names no robot config")
        language("en-GB")
        s = c.get("/api/robot/state")[1]
        assert s["read_only_reason"].startswith("this scenario gives no robot config")
        assert s["errors"][0]["message"] == s["read_only_reason"]
        language("en-US")
        assert c.get("/api/robot/state")[1]["read_only_reason"].startswith("this scenario names no robot config")
    finally:
        app.stop()


def test_a_deleted_robot_config_is_said_again_in_the_new_language(gb, language, tmp_path):
    from scrimmage.assembler.server import App
    from scrimmage.assembler.templates import new_config
    robot = tmp_path / "robot.toml"
    robot.write_text(new_config("standin"))
    p = tmp_path / "s.toml"
    p.write_text('config = "robot.toml"\n\n[[step]]\nmode = "auto"\nduration = 1.0\n')
    app = App(p, open_browser=False, runs_dir=tmp_path / "runs")
    try:
        c = Client(app, app.start())
        assert c.get("/api/scenario/state")[1]["robot_problem"] is None
        robot.unlink()
        end = time.monotonic() + 10
        while "was deleted" not in (c.get("/api/scenario/state")[1]["robot_problem"] or ""):
            assert time.monotonic() < end
            time.sleep(0.1)
        language("en-GB")
        s = c.get("/api/scenario/state")[1]
        assert "has gone" in s["robot_problem"] and "was deleted" not in s["robot_problem"]
        assert "has gone" in c.get("/api/robot/state")[1]["read_only_reason"]
    finally:
        app.stop()


def test_why_a_part_cant_be_edited_follows_the_language(gb, language, part_app, tmp_path):
    c, _ = part_app()
    _built(c)
    (tmp_path / "parts" / "climber.import.toml").unlink()
    st = _wait_for(c, lambda st: st["read_only_reason"] is not None, 5.0)
    assert "was deleted" in st["read_only_reason"]
    language("en-GB")
    assert _state(c)["read_only_reason"] == "climber.import.toml has gone; put it back, or open the CAD file again"


def test_why_a_plugin_part_cant_be_edited_follows_the_language(gb, language, part_app, tmp_path):
    d = _plugin_folder(tmp_path)
    (d / "part.toml").write_text("not [valid")
    c, _ = part_app(d)
    assert _state(c)["read_only_reason"].endswith("; fix it in a text editor")
    language("en-GB")
    assert _state(c)["read_only_reason"].endswith("; mend it in a text editor")


def test_a_failed_build_is_worded_again_in_the_new_language(gb, language):
    """robotcode._failed keeps how to word a failed step (its Check's again()); the page's Build now result and the
    checklist's gradle_built line use it."""
    from scrimmage import robotcode
    c = robotcode._failed("gradle_built", lambda: i18n.t("The robot code didn't build."),
                          lambda: i18n.t("The first build needs the internet to download libraries, and the right "
                                         "Java. The last lines are below."), ["BUILD FAILED"], "gradle_build", 1)
    assert c.message.startswith("The robot code didn't build. ") and c.detail == "BUILD FAILED"
    language("en-GB")
    again = c.again()
    assert again.message.startswith("The robot code wouldn't build. ") and again.detail == "BUILD FAILED"
    import dataclasses
    assert set(dataclasses.asdict(again)) == {"id", "ok", "message", "fix", "detail"}     # again isn't sent


# -- runtime warnings (cleanup A9) -------------------------------------------------------------------------------------
GB_WARNINGS = {
    "_language": "English (UK)",
    "{valve} is on a REV Pneumatic Hub, which needs the lockstep extension ({flag} for RobotPy, the Gradle init "
    "script for Java)": "{valve} sits on a REV Pneumatic Hub, which wants the lockstep extension ({flag})",
    "{valve}: the robot never opened Pneumatic Hub {hub} (it opened Pneumatic Hub {hubs}); check the hub's number in "
    "the valve's {ids_key}|{valve}: the robot never opened Pneumatic Hub {hub} (it opened Pneumatic Hubs {hubs}); "
    "check the hub's number in the valve's {ids_key}": {
        "one": "{valve}: hub {hub} was never opened (only hub {hubs}); see the valve's {ids_key}",
        "other": "{valve}: hub {hub} was never opened (only hubs {hubs}); see the valve's {ids_key}"},
}


@pytest.fixture
def gb_warnings(tmp_path, monkeypatch, language):
    folder = tmp_path / "locales-w"
    folder.mkdir()
    (folder / "en-GB.json").write_text(json.dumps(GB_WARNINGS, ensure_ascii=False), encoding="utf-8")
    shipped = i18n.LOCALES
    monkeypatch.setattr(i18n, "LOCALES", folder)
    i18n.reload()
    language("en-GB")
    yield
    monkeypatch.setattr(i18n, "LOCALES", shipped)
    i18n.reload()


def test_a_runtime_warning_is_machine_text_worded_for_people_when_shown(gb_warnings):
    """A warning made on the simulation's or bridge's thread is machine text (American English, as before: report.json
    and MCP state) and a Said whose words are made when it is shown (the Scenario tab's notes), in the Language then."""
    from scrimmage.checks import shown
    from scrimmage.scenario_runner import Report
    from scrimmage.world import World
    from test_pneumatics_world import ROOT, hal, robot
    w = World(robot("PH:3"), ROOT)
    (msg,) = w.pneumatics.warn_missing_hubs(hal())
    assert msg == ("cyl.valve is on a REV Pneumatic Hub, which needs the lockstep extension "
                   "(`--scrimmage-lockstep` for RobotPy, the Gradle init script for Java)")
    assert shown(msg) == "cyl.valve sits on a REV Pneumatic Hub, which wants the lockstep extension " \
                         "(`--scrimmage-lockstep`)"
    report = Report("s", "", notes=[msg])
    assert report.to_dict()["notes"] == [str(msg)] and msg in w.warnings  # report.json: the machine text
    assert report.to_dict(shown=True)["notes"] == [shown(msg)]           # the page: its words
    with i18n.fixed():
        assert shown(msg) == str(msg)
    w = World(robot({"ph": 2, "channel": 3}), ROOT)
    (msg,) = w.pneumatics.missing_hub_warnings(hal(("ScrimmagePH", "1", {}), ("ScrimmagePH", "3", {})))
    assert shown(msg) == "cyl.valve: hub 2 was never opened (only hubs 1, 3); see the valve's ids"


def test_a_missing_color_sensor_is_worded_when_shown(tmp_path, language):
    import asyncio
    from scrimmage.checks import Said, shown
    from test_sensors_hal import color_bridge
    bridge = color_bridge(tmp_path)
    bridge.hal.devices[("SimDevice", "REV Color Sensor V3[0,82]")] = {}
    asyncio.run(bridge._on_tick(2.0))
    (msg,) = [w for w in bridge.world.warnings if "Color Sensor" in w]
    assert isinstance(msg, Said) and shown(msg) == str(msg)     # en-US: the same words
