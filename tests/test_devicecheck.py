import asyncio
import json
import re
import tomllib
from pathlib import Path

import pytest

from scrimmage.devicecheck import check_devices, device_notes
from scrimmage.protocol import HalState
from scrimmage.scenario import load_scenario
from scrimmage.scenario_runner import NO_CHECKS_NOTE, run_scenario
from scrimmage.world import World

ROOT = Path(__file__).resolve().parents[1]
SWERVE = ROOT / "configs" / "swerve.toml"


@pytest.fixture(scope="module")
def world():
    return World(tomllib.loads(SWERVE.read_text()), SWERVE.parent)


def hal_with(world, ids_swapped=None, drop=(), extra=()):
    """A HalState holding every Phoenix device the world binds, ids changed per ids_swapped, minus `drop`."""
    hal = HalState()
    for typ, name in [m.device_key for m in world.motors] + [e.device_key for e in world.encoders] \
            + [g.device_key for g in world.gyros]:
        m = re.search(r"\[(\d+)\]$", name)
        ident = int(m[1])
        if ident in drop:
            continue
        ident = (ids_swapped or {}).get(ident, ident)
        hal.devices[(typ, re.sub(r"\[\d+\]$", f"[{ident}]", name))] = {}
    for typ, name in extra:
        hal.devices[(typ, name)] = {}
    return hal


def row(rows, slot):
    return next(r for r in rows if r.slot == slot)


def test_all_match(world):
    rows = check_devices(world, hal_with(world), robot_time=5)
    assert rows and all(r.status == "ok" for r in rows)
    assert device_notes(rows) == []


def test_missing_with_suggestion(world):
    rows = check_devices(world, hal_with(world, ids_swapped={3: 13}), robot_time=5)
    r = row(rows, "fr.drive")
    assert r.status == "missing" and r.suggest[0].endswith("13")
    assert r.expected == "TalonFX 3" and r.made is None
    assert "fr.drive expects TalonFX 3, but the robot code made no TalonFX 3; it made TalonFX 13, which nothing uses" \
        in device_notes(rows)
    # the same device is not listed twice: the extra row exists but its note is folded into the missing one
    assert any(x.status == "extra" and x.made == "TalonFX 13" for x in rows)
    assert len(device_notes(rows)) == 1


def test_late_device_not_missing_early(world):
    hal = hal_with(world, drop={3})
    assert row(check_devices(world, hal, robot_time=0.5), "fr.drive").status == "waiting"
    assert row(check_devices(world, hal, robot_time=5), "fr.drive").status == "missing"
    assert device_notes(check_devices(world, hal, robot_time=0.5)) == []


def test_extra_and_other_type(world):
    hal = hal_with(world, drop={3}, extra=[("ScrimmageREV", "SparkMax[3]"), ("ScrimmageREV", "SparkMax[40]")])
    rows = check_devices(world, hal, robot_time=5)
    r = row(rows, "fr.drive")
    assert r.status == "other_type" and r.made == "SPARK MAX 3" and "part inspector" in r.note
    assert any(x.slot is None and x.status == "extra" and x.made == "SPARK MAX 40" for x in rows)


def test_other_binding_kinds_are_not_checked():
    w = World(tomllib.loads((ROOT / "configs" / "kitbot.toml").read_text()), ROOT / "configs")
    rows = check_devices(w, HalState(), robot_time=5)
    assert rows and all(r.status == "not_checked" for r in rows)
    assert device_notes(rows) == []


def _run_ghost(tmp_path, duration):
    text = (ROOT / "tests" / "fake_robot.toml").read_text()
    text += '\n[[motor]]\nname = "ghost"\nctre = "TalonFX:3"\njoint = "left_center"\nmotor = "CIM"\ngear_ratio = 10.71\n'
    robot = tmp_path / "robot.toml"
    robot.write_text(text.replace('"models/kitbot.xml"', f'"{ROOT / "tests" / "models" / "kitbot.xml"}"')
                     .replace('cwd = "."', f'cwd = "{ROOT / "tests"}"'))
    scn = tmp_path / "scn.toml"
    scn.write_text(f'config = "{robot}"\n[[step]]\nname = "idle"\nmode = "disabled"\nduration = {duration}\n')
    return asyncio.run(run_scenario(load_scenario(scn), tmp_path / "out"))


def _assert_in_report(tmp_path, rep, line):
    assert line in rep.notes
    assert line in json.loads((tmp_path / "out" / "report.json").read_text())["notes"]
    assert line in (tmp_path / "out" / "report.md").read_text()
    assert line in rep.summary()


def test_scenario_report_says_the_code_made_none_of_the_devices(tmp_path):
    """The fake robot makes no Phoenix devices at all: the report says so, not just the terminal."""
    rep = _run_ghost(tmp_path, 2.5)
    _assert_in_report(tmp_path, rep, "the robot code made none of the devices the robot file expects (TalonFX 3)")


def test_scenario_report_names_a_device_not_made_before_the_run_ended(tmp_path):
    rep = _run_ghost(tmp_path, 1.0)
    _assert_in_report(tmp_path, rep, "ghost's TalonFX 3 wasn't made yet when the run ended")


def test_waiting_row_is_reported_only_at_the_end(world):
    rows = check_devices(world, hal_with(world, drop={3}), robot_time=0.5)
    assert device_notes(rows) == []
    assert "fr.drive's TalonFX 3 wasn't made yet when the run ended" in device_notes(rows, final=True)


def test_nothing_made_is_one_line(world):
    notes = device_notes(check_devices(world, HalState(), robot_time=5))
    assert len(notes) == 1 and notes[0].startswith("the robot code made none of the devices the robot file expects (TalonFX 1")


def test_other_type_does_not_point_at_a_device_another_slot_binds(world):
    # SPARK MAX 3 made, and the config binds it to another slot: not offered as the wrong-type device
    w = World(tomllib.loads(SWERVE.read_text()), SWERVE.parent)
    hal = hal_with(w, drop={3}, extra=[("ScrimmageREV", "SparkMax[3]")])
    w.motors[0].device_key = ("ScrimmageREV", "SparkMax[3]")
    rows = check_devices(w, hal, robot_time=5)
    assert row(rows, "fr.drive").status == "missing"


def test_scenario_with_no_checks_says_so(tmp_path):
    # a first PASS that tested nothing is misleading (the starter scenario has only a commented-out check)
    rep = _run_ghost(tmp_path, 1.0)
    _assert_in_report(tmp_path, rep, NO_CHECKS_NOTE)


def test_scenario_with_checks_has_no_such_note(tmp_path):
    robot = (ROOT / "tests" / "fake_robot.toml").read_text()
    cfg = tmp_path / "robot.toml"
    cfg.write_text(robot.replace('"models/kitbot.xml"', f'"{ROOT / "tests" / "models" / "kitbot.xml"}"')
                   .replace('cwd = "."', f'cwd = "{ROOT / "tests"}"'))
    scn = tmp_path / "scn.toml"
    scn.write_text(f'config = "{cfg}"\n[[step]]\nname = "idle"\nmode = "disabled"\nduration = 0.5\n'
                   '  [[step.check]]\n  always = {{ signal = "battery", min = 1.0 }}\n'.replace("{{", "{").replace("}}", "}"))
    rep = asyncio.run(run_scenario(load_scenario(scn), tmp_path / "out"))
    assert NO_CHECKS_NOTE not in rep.notes
