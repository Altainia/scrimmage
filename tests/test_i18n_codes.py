"""Errors are placed on their fields by codes, not by their words (spec L3): a robot config error, a warning and a
scenario error land on the same field in a language that reorders their words."""

import json
from pathlib import Path

import pytest

from scrimmage import i18n
from scrimmage.assembler import scenario_validate as sv
from scrimmage.assembler import validate

ROOT = Path(__file__).resolve().parent.parent
SWERVE = (ROOT / "configs" / "swerve.toml").read_text()
SCENARIO = ROOT / "scenarios" / "x.toml"
HEAD = 'config = "../configs/kitbot.toml"\n'
STEP = '\n[[step]]\nname = "A"\nmode = "auto"\nduration = 1.0\n'

# Each translation moves the words around the placeholders, as another language might
GB = {
    "_language": "English (UK)",
    "{where}: attach {attach}: no mount or body {local} on {target}; mounts: {mounts}; bodies: {bodies}":
        "ZZ {local} {target} ({where})",
    "{where}: ids: {slot} = {value}: expected \"<Kind>:<number>\", e.g. \"TalonFX:3\"": "ZZ {value} {slot} {where}",
    "{where}: {name} must be at most {most}, got {got}": "ZZ {got} > {most}: {name} ({where})",
    "{part}.{slot} has no device ID, so it won't report": "ZZ {slot} of {part}",
    "{device} is bound by {a} and {b}: give each device one binding": "ZZ {b} {a} {device}",
    "{file}, step {n} ({name})": "{name} ZZ {n} {file}",
    "mode must be one of {modes}, not {value}": "ZZ {value} ({modes})",
    "{step}, screenshot {n}": "ZZ {n} {step}",
    "at = {at} is beyond the step's {duration} s": "ZZ {duration} < {at}",
    "unknown signal {signal}; signals are {signals}": "ZZ {signal}",
    "{key} must be a number, not {value}": "ZZ {value} {key}",
    "station must be one of {stations}, not {value}": "ZZ {value}",
}


@pytest.fixture
def gb(tmp_path, monkeypatch, language):
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


def _robot(tmp_path, old, new):
    assert old in SWERVE
    return validate.check(SWERVE.replace(old, new, 1), tmp_path / "robot.toml")


@pytest.mark.parametrize("old,new,field", [
    ('attach = "drive.front_left"', 'attach = "drive.nowhere"', "fl/attach"),
    ('encoder = "CANcoder:9"', 'encoder = "nine"', "fl/ids:encoder"),
    ("module_inset = 0.0579", "module_inset = 5.0", "drive/param:module_inset"),
])
def test_a_robot_error_lands_on_its_field_in_another_language(gb, tmp_path, old, new, field):
    res = _robot(tmp_path, old, new)
    assert [e.field for e in res.errors] == [field]
    assert res.errors[0].message.startswith("ZZ"), res.errors[0].message


def test_a_device_bound_twice_lands_on_both_fields(gb, tmp_path):
    res = _robot(tmp_path, "CANcoder:10", "CANcoder:9")
    assert sorted(e.field for e in res.errors) == ["fl/ids:encoder", "fr/ids:encoder"]
    assert all(e.message.startswith("ZZ") for e in res.errors)


def test_a_warning_lands_on_its_field_in_another_language(gb, tmp_path):
    res = _robot(tmp_path, ', encoder = "CANcoder:9"', "")
    assert res.ok and [w.field for w in res.warnings] == ["fl/ids:encoder"]
    assert res.warnings[0].message == "ZZ encoder of fl"


@pytest.mark.parametrize("text,field,detail", [
    (HEAD + STEP.replace('"auto"', '"bogus"'), "step0/mode", "ZZ 'bogus' (disabled, auto, teleop, test)"),
    (HEAD + STEP + '  [[step.screenshot]]\n  at = 5.0\n', "step0/shot0/at", "ZZ 1 < 5.0"),
    (HEAD + STEP + '  [[step.check]]\n  at_end = { signal = "nope", min = 1 }\n', "step0/check0/at_end.signal",
     "ZZ 'nope'"),      # a signal's error says no where: all of it is its detail
    (HEAD + STEP + '  [[step.check]]\n  at_end = { signal = "battery", min = "x" }\n', "step0/check0/at_end.min",
     "ZZ 'x' min"),
    (HEAD + '[match]\nstation = "red4"\n' + STEP, "scenario/match.station", "ZZ 'red4'"),
])
def test_a_scenario_error_lands_on_its_field_in_another_language(gb, text, field, detail):
    r = sv.check(text, SCENARIO)
    assert [e.field for e in r.errors] == [field], r.errors
    e = r.errors[0]
    assert "ZZ" in e.message, e.message
    assert e.detail == detail
    if field.startswith("step0/") and detail != e.message:      # a message with its where
        assert "A ZZ 1 x.toml" in e.message      # the where, in the language's own order


def test_the_same_scenario_error_in_american_english(language):
    language("en-US")
    r = sv.check(HEAD + STEP + '  [[step.screenshot]]\n  at = 5.0\n', SCENARIO)
    e = r.errors[0]
    assert (e.field, e.message, e.detail) == ("step0/shot0/at",
                                             "x.toml, step 1 (A), screenshot 1: at = 5.0 is beyond the step's 1 s",
                                             "at = 5.0 is beyond the step's 1 s")
