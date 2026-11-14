"""The inspection report (scrimmage/inspection/report.py) and `scrimmage inspect`: one line per limit, worded twice
(machine text in SI, shown text in the Units setting), and the exit codes."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scrimmage import units
from scrimmage.checks import Said, shown
from scrimmage.config import ConfigError, build_world, load_config
from scrimmage.inspection.report import inspect_world, warnings
from scrimmage.inspection.spec import SEASONS

ROOT = Path(__file__).resolve().parent.parent
LB, IN = 0.45359237, 0.0254

CHASSIS = '[[part]]\nname = "drive"\nstock = "chassis"\n'      # the default chassis: 0.7 x 0.7 m, 46 kg


def config(tmp_path, text=CHASSIS, name="robot.toml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def inspect(path, override=None):
    c = load_config(path)
    return inspect_world(build_world(c), c.raw, override)


def line(report, key):
    return next(x for x in report.lines if x.key == key)


def test_the_default_chassis_is_just_over_2026s_perimeter(tmp_path):
    c = config(tmp_path)
    over = line(inspect(c, 2026), "perimeter")
    assert over.over == pytest.approx(0.006, abs=1e-6) and over.measured == pytest.approx(2.8)
    assert over.limit == pytest.approx(110 * IN) and over.rule == "R104"
    assert over in inspect(c, 2026).over
    within = line(inspect(c, 2024), "perimeter")
    assert within.over <= 0 and within not in inspect(c, 2024).over
    with units.fixed("imperial"):
        assert "110.24 in" in shown(over.text) and "by 0.24 in" in shown(over.text)
    assert str(over.text).startswith("frame perimeter: 2800 mm")      # the machine text: SI


def test_the_weight_line_shows_what_it_subtracted(tmp_path):
    w = line(inspect(config(tmp_path), 2026), "weight")
    assert w.estimate and w.measured == pytest.approx(46.0 - 12.89 * LB - 15 * LB)
    with units.fixed("imperial"):
        text = shown(w.text)
    assert "101.4 lb modeled" in text
    assert "12.89 lb battery, estimate" in text and "15 lb bumpers, estimate" in text
    set_ = config(tmp_path, CHASSIS + "[inspection]\nbattery_mass = 5.0\nbumper_mass = 6.0\n", "set.toml")
    w = line(inspect(set_, 2026), "weight")
    assert not w.estimate and w.measured == pytest.approx(46.0 - 11.0)
    assert "estimate" not in str(w.text)
    assert not line(inspect(set_, 2026), "weight_with_bumpers").estimate
    assert line(inspect(set_, 2026), "weight_with_bumpers").measured == pytest.approx(46.0 - 5.0)


def test_an_over_weight_on_estimates_says_so(tmp_path):
    heavy = config(tmp_path, CHASSIS.replace("stock", "mass = 80.0\nstock"))
    r = inspect(heavy, 2026)
    w = line(r, "weight")
    assert w.over > 0 and w.estimate and w in r.over
    assert "weigh your battery and bumpers" in str(w.text) and "battery_mass" in str(w.text)
    assert "weigh your battery" in " ".join(warnings(r))


def test_subtracting_more_than_the_model_weighs_is_a_warning(tmp_path):
    c = config(tmp_path, CHASSIS + "[inspection]\nbattery_mass = 40.0\nbumper_mass = 20.0\n")
    r = inspect(c, 2026)
    ws = warnings(r)
    assert any("less than" in w and "battery_mass" in w for w in ws)
    assert all(isinstance(w, Said) for w in ws)


def test_a_season_without_a_limit_says_so(tmp_path):
    c = config(tmp_path)
    h = line(inspect(c, 2025), "height")
    assert h.measured is None and h.limit is None and h.over <= 0
    assert "no limit in 2025" in str(h.text) and h not in inspect(c, 2025).over
    r = line(inspect(c, 2013), "reach")
    assert r.measured is None and r.limit is None and "no limit in 2013" in str(r.text)
    assert r not in inspect(c, 2013).over
    assert any(x.key == "diameter" for x in inspect(c, 2013).lines)
    assert not any(x.key == "diameter" for x in inspect(c, 2024).lines)
    assert not any(x.key == "bumper_set" for x in inspect(c, 2026).lines)
    assert any(x.key == "bumper_set" for x in inspect(c, 2024).lines)


def test_unless_is_quoted(tmp_path):
    text = (ROOT / "configs" / "elevator_arm.toml").read_text(encoding="utf-8").replace("travel = 1.0", "travel = 2.5")
    r = inspect(config(tmp_path, text), 2022)
    h = line(r, "height")
    assert h.over > 0 and h in r.over
    assert "hangar zone" in str(h.text) and "not checked" in str(h.text)


def test_the_start_reach_names_the_part(tmp_path):
    r = inspect(ROOT / "configs" / "maxswerve.toml", 2026)
    s = line(r, "start_reach")
    assert s.over > 0 and s in r.over and s.limit == pytest.approx(0.25 * IN)
    with units.fixed("imperial"):
        text = shown(s.text)
    assert text.startswith("start reach: 1.7 in past the frame (bl.base); the rules want it within the frame")
    assert "1/4 in" in text or "0.25 in" in text
    ok = line(inspect(config(tmp_path), 2024), "start_reach")
    assert ok.over <= 0 and "within" in str(ok.text)


def test_without_a_frame_plate_the_perimeter_line_says_so(tmp_path):
    (tmp_path / "robot.xml").write_text(
        '<mujoco><worldbody><body name="chassis" pos="0 0 0.2"><freejoint/>'
        '<geom type="cylinder" size="0.3 0.1" mass="40"/></body></worldbody></mujoco>', encoding="utf-8")
    r = inspect(config(tmp_path, '[model]\nmjcf = "robot.xml"\n'), 2026)
    assert "no frame plate" in str(line(r, "perimeter").text)


def test_lines_follow_the_units_setting(tmp_path):
    p = line(inspect(config(tmp_path), 2026), "perimeter")
    with units.fixed("metric"):
        assert "2800 mm" in shown(p.text) and " in" not in shown(p.text)
    with units.fixed("imperial"):
        assert "110.24 in" in shown(p.text)
    w = line(inspect(config(tmp_path), 2026), "weight")
    with units.fixed("metric"):
        assert "kg" in shown(w.text) and "lb" not in shown(w.text)
    with units.fixed("imperial"):
        assert "lb" in shown(w.text) and "kg" not in shown(w.text)


def test_the_inspection_names_why_it_chose_the_season(tmp_path):
    r = inspect(config(tmp_path))
    assert r.season.year == max(SEASONS) and r.reason == "the newest season"
    assert r.not_checked == SEASONS[2026].not_checked


def test_a_bad_setting_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        inspect(config(tmp_path, CHASSIS + "[inspection]\nbattery_mass = -1\n"))


def run_cli(*args, cwd=ROOT):
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run([sys.executable, "-m", "scrimmage", *args], capture_output=True, text=True, cwd=cwd,
                          env=env, encoding="utf-8")


def test_cli_exit_codes(tmp_path):
    small = config(tmp_path, '[[part]]\nname = "drive"\nstock = "chassis"\nlength = 0.5\nwidth = 0.5\nmass = 40.0\n'
                   'bumper = 0.05\n')
    ok = run_cli("inspect", str(small), "--season", "2024")
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "2024 rules (from --season)" in ok.stdout and "Not checked:" in ok.stdout
    assert "Limits from 2024 Game Manual" in ok.stdout and "https://" in ok.stdout
    over = run_cli("inspect", str(config(tmp_path, name="default.toml")), "--season", "2026")
    assert over.returncode == 1 and "over the 110 in limit" in over.stdout
    default = run_cli("inspect", str(small))
    assert "the newest season; set [inspection] season to choose" in default.stdout
    bad = run_cli("inspect", str(config(tmp_path, "[[part]]\nname = 1\n", "bad.toml")))
    assert bad.returncode == 2 and bad.stderr.startswith("scrimmage: ")
    s = run_cli("inspect", str(small), "--season", "2019")
    assert s.returncode == 2 and "--season 2019: scrimmage knows the seasons 2013, 2016" in s.stderr
    assert "[inspection]" not in s.stderr
    assert run_cli("inspect", str(tmp_path / "missing.toml")).returncode == 2
