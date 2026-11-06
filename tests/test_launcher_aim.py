"""The aiming table from the launcher model: rows hit the goal center, tables repeat, the outputs, unreachable
distances."""

from __future__ import annotations

import subprocess
import sys

import pytest
import tomlkit

from range_fixtures import shooter_cfg
from scrimmage.launcher.report import uncalibrated
from scrimmage.testrange import aim

GOAL = "hub_blue"


def write(tmp_path, **launcher):
    # a tighter hood than the part's 0.12 m default: that one tops out near 6 m/s and cannot reach the 1.83 m rim
    path = tmp_path / "robot.toml"
    path.write_text(tomlkit.dumps(shooter_cfg(launcher={"hood_gap": 0.10, **launcher})), encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def cfg_path(tmp_path_factory):
    return write(tmp_path_factory.mktemp("aim"))


@pytest.fixture(scope="module")
def goal_name(cfg_path):
    from scrimmage.config import load_config
    from scrimmage.parts.assemble import assemble
    cfg = load_config(cfg_path)
    return assemble(cfg.raw, cfg.dir).range.goals[0].name


@pytest.fixture(scope="module")
def table(cfg_path, goal_name):
    return aim.aim_table(cfg_path, goal_name, [2.5, 3.0], hood_step_deg=10.0)


def cli(*args):
    return subprocess.run([sys.executable, "-m", "scrimmage", "launcher", "aim", *map(str, args)],
                          capture_output=True, text=True)


def test_rows_hit_goal_center(cfg_path, goal_name, table):
    rows = table
    assert [r.distance for r in rows] == [2.5, 3.0]
    model = aim.load_model(cfg_path, goal_name, 10.0)
    for row in rows:
        landing = aim.fly_row(model, row)
        assert landing.at_goal is not None
        assert abs(landing.at_goal[0] - (row.distance - model.forward)) < 0.05
        assert landing.at_goal[1] == pytest.approx(model.lane)     # the lane's offset across the robot
        assert row.flight_s == pytest.approx(landing.goal_time)
        assert row.apex == pytest.approx(landing.apex)
        assert 0.0 < row.flight_s < landing.flight_time


def test_first_row_steepest_later_rows_follow_wheel_speed(cfg_path, goal_name, table):
    """The first row is the highest apex; each later one the solution nearest the previous row's wheel speed and hood
    angle, so a grip-limited plateau (where many wheel speeds give the same shot) can't make neighboring rows jump."""
    model = aim.load_model(cfg_path, goal_name, 10.0)
    found = [[r for a in model.hood_angles if (r := aim._solve(model, a, d - model.forward, (10.0, 120.0)))]
             for d in (2.5, 3.0)]
    assert table[0] == max(found[0], key=lambda r: (r.apex, -r.wheel_rps))
    near = lambda r: abs(r.wheel_rps - table[0].wheel_rps) + abs(r.hood_deg - table[0].hood_deg) / aim.HOOD_PER_RPS
    assert table[1] == min(found[1], key=near)


def test_choose_prefers_the_previous_row():
    row = lambda rps, apex, hood=60.0: aim.AimRow(3.0, rps, hood, 1.0, apex)
    plateau = [row(99.0, 3.4), row(57.0, 3.2), row(55.0, 3.3)]
    assert aim.choose(plateau, None) == plateau[0]                  # first row: steepest
    assert aim.choose(plateau, row(56.4, 3.0)) == plateau[1]        # then: nearest 56.4 rps, not the 99 rps apex
    assert aim.choose(plateau, row(56.0, 3.0)) == plateau[2]        # 57 and 55 tie: the higher apex, 55 rps
    assert aim.choose([], row(56.0, 3.0)) is None
    # the hood counts too: 1 rps closer isn't worth a 30 deg jump to a flat shot
    flat, steep = row(50.7, 1.83, 40.0), row(51.9, 3.0, 68.0)
    assert aim.choose([flat, steep], row(50.7, 3.0, 70.0)) == steep


def test_table_repeatable(cfg_path, goal_name, table):
    a = aim.to_csv(table)
    b = aim.to_csv(aim.aim_table(cfg_path, goal_name, [2.5, 3.0], hood_step_deg=10.0))
    assert a == b
    assert a.splitlines()[0] == "distance,wheel_rps,hood_deg,flight_s,apex,status"
    assert len(a.splitlines()) == 3


def test_java_output_compiles_shape(cfg_path, goal_name, tmp_path):
    out = tmp_path / "AimTable.java"
    r = cli(cfg_path, "--goal", goal_name, "--distances", "3:3:1", "--hood-step", "10",
            "--java", out, "--java-class", "frc.robot.AimTable")
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    assert "package frc.robot;" in text
    assert "public final class AimTable {" in text
    assert "public static final double[][] ROWS = {" in text
    assert uncalibrated() in text
    assert "scrimmage launcher aim CONFIG" in text and str(tmp_path) not in text
    assert sum(1 for line in text.splitlines() if line.startswith("        {")) == 1


def test_cli_csv(cfg_path, goal_name, tmp_path):
    out = tmp_path / "aim.csv"
    r = cli(cfg_path, "--goal", goal_name, "--distances", "3:3:1", "--hood-step", "10", "--csv", out)
    assert r.returncode == 0, r.stderr
    assert out.read_text().splitlines()[0] == "distance,wheel_rps,hood_deg,flight_s,apex,status"
    assert "3.0000" in r.stdout


def test_unreachable_among_reachable_is_marked(cfg_path, goal_name, tmp_path):
    csv, java = tmp_path / "a.csv", tmp_path / "A.java"
    r = cli(cfg_path, "--goal", goal_name, "--distances", "3:30:27", "--hood-step", "10", "--csv", csv,
            "--java", java, "--java-class", "A")
    assert r.returncode == 0, r.stderr
    assert r.stderr.count("warning") == 1 and "30 m can't be reached" in r.stderr
    assert "reachable distances are about" in r.stderr
    lines = csv.read_text().splitlines()
    assert lines[2] == "30.0000,,,,,unreachable"
    assert lines[1].endswith(",ok") and lines[1].startswith("3.0000,")
    text = java.read_text()
    assert "// 30 m: unreachable, left out" in text
    assert sum(1 for line in text.splitlines() if line.startswith("        {")) == 1


def test_unreachable_distance_explains(cfg_path, goal_name):
    r = cli(cfg_path, "--goal", goal_name, "--distances", "30:30:1", "--hood-step", "10")
    assert r.returncode == 2
    assert r.stderr.startswith("scrimmage: ")
    assert "30 m" in r.stderr and "reachable distances are about" in r.stderr


def test_bad_input_exits_2(cfg_path, goal_name):
    assert cli(cfg_path, "--goal", "nope", "--distances", "2:3:1").returncode == 2
    assert cli(cfg_path, "--goal", goal_name, "--distances", "3:2:1").returncode == 2
    assert cli(cfg_path, "--goal", goal_name, "--distances", "2:3").returncode == 2
    assert cli(cfg_path, "--goal", goal_name, "--distances", "2:3:0").returncode == 2


def test_fixed_hood_single_angle(tmp_path):
    path = write(tmp_path, hood="fixed", hood_angle=50.0, ids={"shooter": "PWM:2"})
    model = aim.load_model(path, GOAL)
    assert model.hood_angles == (50.0,)
    for row in aim.aim_table(path, GOAL, [2.5]):
        assert row.hood_deg == 50.0


def test_cli_regeneration_is_byte_identical(cfg_path, goal_name, tmp_path):
    outs = []
    for name in ("one", "two"):
        d = tmp_path / name
        d.mkdir()
        cfg = d / "robot.toml"
        cfg.write_bytes(cfg_path.read_bytes())
        r = cli(cfg, "--goal", goal_name, "--distances", "3:3:1", "--hood-step", "10",
                "--csv", d / "aim.csv", "--java", d / "AimTable.java", "--java-class", "frc.robot.AimTable")
        assert r.returncode == 0, r.stderr
        outs.append(((d / "aim.csv").read_bytes(), (d / "AimTable.java").read_bytes()))
    assert outs[0] == outs[1]
