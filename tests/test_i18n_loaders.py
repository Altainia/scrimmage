"""The input files' errors in another language (sub-project 20b, Task 6d): the range, shooter, launcher and disc
setups, climb scripts, sensors, bindings, joystick tracks, the robot-code device check and foreign sim writes. Each
follows the Language setting, and nothing reads their words back (spec L3): the Bench tab's edit refusal knows a
missing entry by its code, and summary.json says where the headlines start when the notes run over lines. A run's
report.json keeps the device notes in American English while report.md shows them in the Language (spec L2).

The catalog is these tests' own (a scratch locales folder), so it can change words en-GB spells the same."""
import asyncio
import json
import re
import tomllib
from pathlib import Path

import pytest

from scrimmage import i18n

ROOT = Path(__file__).resolve().parents[1]
PINCH = ROOT / "examples" / "bench" / "pinch_car.toml"

GB = {
    "_language": "English (UK)",
    # the range and the shooter
    "give ball or piece, not both": "a ball or a piece, not the pair",
    "must be a table": "has to be a table",
    # launcher and disc setups (one source for both)
    "unknown section [{name}]; valid sections: {sections}": "there's no section [{name}]; the sections are {sections}",
    # climb scripts
    "{path}: a climb script needs at least one {step_table}": "{path}: a climb script wants a {step_table} or more",
    # sensors
    "needs {kind_setting} or {last}": "wants {kind_setting} or else {last}",
    # bindings, and a list separator
    "{what} sets {given}: give only one of {keys}": "{what} sets {given}: pick one of {keys}",
    "gyro": "gyroscope",
    "list separator\u0004 and ": " plus ",
    # joystick tracks
    "{file}: line {line}: {problem}": "{file}, line {line}: {problem}",
    # the device check and foreign writes
    "{slot} expects {device}, but the robot code made no {device}; it made {made}, which nothing uses":
        "{slot} wants {device}; the robot programme made {made} instead, which nothing uses",
    "the robot code made none of the devices the robot file expects ({devices})":
        "the robot programme made none of the devices the robot file wants ({devices})",
    "rotor velocity": "rotor speed",
    # the Bench tab's edit refusal: the words drop "named"
    "no {section} named '{name}' in '{dotted}'; {section}s: {known}": "there's no {section} called '{name}' "
                                                                       "({dotted}); {section}s: {known}",
    "{problem}; if it comes from an included file, override it first":
        "{problem}; override it first if an included file gives it",
}


@pytest.fixture
def gb(tmp_path, monkeypatch, language):
    """The Language setting en-GB, read from a scratch locales folder holding GB."""
    folder = tmp_path / "locales"
    folder.mkdir()
    (folder / "en-GB.json").write_text(json.dumps(GB, ensure_ascii=False), encoding="utf-8")
    shipped = i18n.LOCALES
    monkeypatch.setattr(i18n, "LOCALES", folder)
    i18n.reload()
    language("en-GB")
    yield
    monkeypatch.setattr(i18n, "LOCALES", shipped)
    i18n.reload()


def _both(fn, exc):
    """The message `fn` raises in the Language setting, then in American English."""
    with pytest.raises(exc) as shown:
        fn()
    with i18n.fixed(), pytest.raises(exc) as us:
        fn()
    return str(shown.value), str(us.value)


def test_range_and_shooter_errors_follow_the_language(gb, tmp_path):
    from scrimmage.testrange.shooter import parse_shooter
    from scrimmage.testrange.spec import RangeError, load_range
    assert _both(lambda: load_range("2026", tmp_path, ball="a", piece="b"), RangeError) == (
        "a ball or a piece, not the pair", "give ball or piece, not both")
    assert _both(lambda: parse_shooter({"shooter": 1}, None, None), ValueError) == (
        "[shooter] has to be a table", "[shooter] must be a table")


def test_launcher_and_disc_setup_errors_follow_the_language(gb):
    from scrimmage.disc.inputs import DiscConfigError
    from scrimmage.disc.inputs import parse_setup as disc_setup
    from scrimmage.launcher.inputs import LauncherConfigError, parse_setup
    shown, us = _both(lambda: parse_setup({"bogus": {}}), LauncherConfigError)
    assert shown.startswith("there's no section [bogus]; the sections are ") and \
        us.startswith("unknown section [bogus]; valid sections: ")
    shown, us = _both(lambda: disc_setup({"bogus": {}}), DiscConfigError)
    assert shown.startswith("there's no section [bogus]; the sections are ") and \
        us.startswith("unknown section [bogus]; valid sections: ")


def test_climb_script_errors_follow_the_language(gb):
    from scrimmage.climb.script import ScriptError, parse_script
    assert _both(lambda: parse_script({}, "s.toml"), ScriptError) == (
        "s.toml: a climb script wants a [[step]] or more", "s.toml: a climb script needs at least one [[step]]")


def test_sensor_errors_follow_the_language(gb):
    from scrimmage.sensors.spec import SensorError, parse_sensor
    assert _both(lambda: parse_sensor({"name": "a"}, 1), SensorError) == (
        '[[sensor]] "a": wants kind = "beam", "distance" or else "color"',
        '[[sensor]] "a": needs kind = "beam", "distance" or "color"')


def test_binding_errors_follow_the_language_and_its_list_separator(gb):
    from scrimmage.bindings import _one_of
    assert _both(lambda: _one_of({"ctre": "Pigeon2:1", "device": "x"}, ("ctre", "scrimmage", "device"),
                                 i18n.t("gyro")), ValueError) == (
        "gyroscope sets ctre plus device: pick one of ctre, scrimmage, device",
        "gyro sets ctre and device: give only one of ctre, scrimmage, device")


def test_track_errors_follow_the_language(gb):
    from scrimmage.tracks import TrackError, parse_track
    shown, us = _both(lambda: parse_track("", Path("drive.csv")), TrackError)
    assert shown.startswith("drive.csv, line 1: the file is empty") and us.startswith("drive.csv: line 1: the file")


def test_device_check_and_foreign_writes_follow_the_language(gb):
    from scrimmage import foreign_writes as fwn
    from scrimmage.devicecheck import check_devices, device_notes
    from scrimmage.protocol import HalState
    from scrimmage.world import World
    swerve = ROOT / "configs" / "swerve.toml"
    world = World(tomllib.loads(swerve.read_text()), swerve.parent)
    hal = HalState()
    for typ, name in [m.device_key for m in world.motors] + [e.device_key for e in world.encoders] \
            + [g.device_key for g in world.gyros]:
        hal.devices[(typ, re.sub(r"\[3\]$", "[13]", name))] = {}
    notes = device_notes(check_devices(world, hal, robot_time=5))
    assert "fr.drive wants TalonFX 3; the robot programme made TalonFX 13 instead, which nothing uses" in notes
    with i18n.fixed():
        notes = device_notes(check_devices(world, hal, robot_time=5))
    assert "fr.drive expects TalonFX 3, but the robot code made no TalonFX 3; it made TalonFX 13, which nothing " \
           "uses" in notes
    writes = [{"device": "Talon FX (v6)[1]/Rotor Sensor", "key": ">velocity", "first_t": 0.5}]
    assert "(rotor speed, first at t=0.500)" in fwn.message("Talon FX (v6)[1]", writes, "loaded")
    with i18n.fixed():
        assert "(rotor velocity, first at t=0.500)" in fwn.message("Talon FX (v6)[1]", writes, "loaded")


def test_a_runs_device_notes_are_american_english_in_report_json_and_the_language_in_report_md(gb, tmp_path):
    """An MCP-style run (inside i18n.machine()): report.json keeps the device note's machine words, and report.md,
    a file for people, shows it in the Language (the note is a Said, built in both at the run's end)."""
    from scrimmage.scenario import load_scenario
    from scrimmage.scenario_runner import run_scenario
    text = (ROOT / "tests" / "fake_robot.toml").read_text()
    text += '\n[[motor]]\nname = "ghost"\nctre = "TalonFX:3"\njoint = "left_center"\nmotor = "CIM"\ngear_ratio = 10.71\n'
    robot = tmp_path / "robot.toml"
    robot.write_text(text.replace('"models/kitbot.xml"', f'"{ROOT / "tests" / "models" / "kitbot.xml"}"')
                     .replace('cwd = "."', f'cwd = "{ROOT / "tests"}"'))
    scn = tmp_path / "scn.toml"
    scn.write_text(f'config = "{robot}"\n[[step]]\nname = "idle"\nmode = "disabled"\nduration = 2.5\n')
    with i18n.machine():
        asyncio.run(run_scenario(load_scenario(scn), tmp_path / "out"))
    us = "the robot code made none of the devices the robot file expects (TalonFX 3)"
    shown = "the robot programme made none of the devices the robot file wants (TalonFX 3)"
    assert us in json.loads((tmp_path / "out" / "report.json").read_text())["notes"]
    md = (tmp_path / "out" / "report.md").read_text()
    assert shown in md and us not in md


def test_a_bench_edit_refusal_knows_a_missing_entry_by_its_code(gb):
    """The refusal's hint for an entry from an included file follows the BenchError's code, not the word "named"
    (the en-GB words here drop it)."""
    import tomlkit
    from scrimmage.assembler.bench_edits import set_value
    from scrimmage.assembler.edits import EditError
    doc = tomlkit.parse(PINCH.read_text(encoding="utf-8"))
    shown, us = _both(lambda: set_value(doc, "wheel.nope.diameter", 1), EditError)
    assert "called 'nope'" in shown and shown.endswith("; override it first if an included file gives it")
    assert "named 'nope'" in us and us.endswith("; if it comes from an included file, override it first")


def test_the_bench_tab_finds_the_headlines_after_notes_with_blank_lines(tmp_path):
    """Notes that run over lines, a blank one among them: summary.json's title_lines says where the title block
    ends, so the Bench tab still reads the headlines (and their UNCALIBRATED line) by position."""
    from scrimmage.assembler.bench_session import _summary
    from scrimmage.bench.report import headlines, write_results
    from scrimmage.bench.run import run_bench
    from scrimmage.bench.spec import load_bench
    text = PINCH.read_text(encoding="utf-8").replace(
        'idea = "two wheels pinch the car down the track"',
        'idea = "two wheels pinch the car down the track"\nnotes = """first line\n\nthird line, after a blank one"""')
    f = tmp_path / "notes.toml"
    f.write_text(text, encoding="utf-8")
    result = run_bench(load_bench(f))
    out = write_results(result, tmp_path / "r")
    heads = headlines(result)
    lines = (out / "summary.txt").read_text(encoding="utf-8").splitlines()
    assert lines[:5] == ["pinch car (Maya)", 'idea: "two wheels pinch the car down the track"', "first line", "",
                         "third line, after a blank one"]
    assert json.loads((out / "summary.json").read_text(encoding="utf-8"))["title_lines"] == 5
    assert _summary(out) == (heads, heads[-1])
