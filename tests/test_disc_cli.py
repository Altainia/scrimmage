"""scrimmage disc run | sweep | table, the example setups, and the exit-to-flight conversion."""

import csv
import math
from pathlib import Path

import numpy as np
import pytest

from scrimmage.__main__ import main
from scrimmage.disc.flight import fly, matrix_from_quat
from scrimmage.disc.inputs import DiscConfigError, load_setup, parse_setup
from scrimmage.disc.launcher import ExitedDisc, DiscEnergy
from scrimmage.disc.run import METRIC_KEYS, exit_to_flight_state, run_setup
from scrimmage.disc.report import disc_uncalibrated

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "disc"
NAMES = ("linear.toml", "arc.toml", "pinch.toml")
INCH = 0.0254


def quick(name: str, tmp_path: Path, extra: str = "") -> Path:
    """A copy of an example with one disc to fire, to keep the test quick (a disc's flight is the slow part)."""
    text = (EXAMPLES / name).read_text().replace("count = 4", "count = 1")
    assert "count = 1" in text
    p = tmp_path / name
    p.write_text(text + extra)
    return p


def shot(speed=10.0, spin=100.0, yaw=0.0, y=0.0) -> ExitedDisc:
    n = math.nan
    return ExitedDisc(0, "launched", 0.0, 0.1, speed, spin, yaw, (0.4, y), DiscEnergy(n, n, n, n, n, n, n), 0.0)


def setup_with(tilt=0.0, height=1.0, vel=(0.0, 0.0)):
    s = load_setup(EXAMPLES / "linear.toml")[0]
    import dataclasses
    return dataclasses.replace(s, mount=dataclasses.replace(s.mount, height=height, tilt_deg=tilt),
                               robot=dataclasses.replace(s.robot, velocity=vel))


def test_exit_state_level():
    st = exit_to_flight_state(shot(), setup_with(tilt=0.0, height=1.0))
    assert st.pos == pytest.approx([0.0, 0.0, 1.0])
    assert st.vel == pytest.approx([10.0, 0.0, 0.0])
    assert matrix_from_quat(st.quat) == pytest.approx(np.eye(3), abs=1e-12)
    assert st.omega_body == pytest.approx([0.0, 0.0, 100.0])  # counter-clockwise from above is +z


def test_exit_state_tilted_and_yawed():
    st = exit_to_flight_state(shot(yaw=10.0), setup_with(tilt=30.0))
    t, y = math.radians(30.0), math.radians(10.0)
    # the velocity is in the disc plane: perpendicular to the disc's axis
    axis = matrix_from_quat(st.quat)[:, 2]
    assert float(st.vel @ axis) == pytest.approx(0.0, abs=1e-9)
    assert st.vel[2] == pytest.approx(10.0 * math.cos(y) * math.sin(t))
    assert st.vel[1] == pytest.approx(10.0 * math.sin(y))
    assert axis == pytest.approx([-math.sin(t), 0.0, math.cos(t)])  # nose-up tilt leans the top back


def test_exit_state_adds_robot_velocity():
    st = exit_to_flight_state(shot(), setup_with(vel=(1.5, -0.5)))
    assert st.vel == pytest.approx([11.5, -0.5, 0.0])


def test_examples_run(tmp_path, capsys):
    for name in NAMES:
        out = tmp_path / "out" / name
        main(["disc", "run", str(quick(name, tmp_path)), "--out", str(out)])
        assert (out / "results.csv").exists() and (out / "summary.md").exists(), name
        text = capsys.readouterr().out
        assert text.splitlines()[0] == disc_uncalibrated()
        rows = list(csv.DictReader((out / "results.csv").open()))
        assert len(rows) == 1 and float(rows[0]["launched"]) >= 1, name
        assert float(rows[0]["exit_speed"]) > 3.0, name


def test_linear_example_scores():
    setup, _, _ = load_setup(EXAMPLES / "linear.toml")
    r = run_setup(setup)
    assert r.metrics["launched"] == setup.feed.count and r.metrics["scored"] == setup.feed.count
    surface = 2 * math.pi * setup.controls[0].target_rps * setup.wheels.diameter / 2
    assert 0.35 <= r.metrics["exit_speed"] / surface <= 0.48  # the 2013 multi-wheel band
    assert r.metrics["spin_rpm"] > 0.0  # a wheel on the right spins the disc counter-clockwise


def test_metric_keys_and_trace(tmp_path):
    r = run_setup(load_setup(quick("arc.toml", tmp_path))[0])
    assert set(r.metrics) == set(METRIC_KEYS)
    assert r.trace and r.trace[0][0] == 0.0
    assert r.shots[0][0].status == "launched" and r.shots[0][1] is not None


def test_sweep_over_wheel_rpm(tmp_path, capsys):
    p = quick("arc.toml", tmp_path, '\n[sweep]\n"control.target_rps" = [30.0, 40.0, 50.0, 90.0]\n')
    out = tmp_path / "o"
    main(["disc", "sweep", str(p), "--out", str(out)])
    rows = list(csv.DictReader((out / "results.csv").open()))
    assert [r["control.target_rps"] for r in rows] == ["30", "40", "50", "90"]
    speeds = [float(r["exit_speed"]) for r in rows]
    # Faster wheels launch faster until the grip saturates: friction mu N is then the same at any wheel speed.
    assert speeds[0] < speeds[1] < speeds[2] <= speeds[3] + 1e-6
    assert list(rows[0])[1:] == list(METRIC_KEYS)


def test_linear_example_sweeps_tilt(tmp_path, capsys):
    p = quick("linear.toml", tmp_path)
    out = tmp_path / "o"
    main(["disc", "sweep", str(p), "--out", str(out)])
    rows = list(csv.DictReader((out / "results.csv").open()))
    heights = [float(r["wall_height_m"]) for r in rows]
    assert len(rows) == 5 and heights == sorted(heights)  # a higher tilt arrives higher
    assert 0.0 < sum(float(r["scored"]) for r in rows) < 5  # the slot is a band: some tilts miss


def test_sweep_without_sweep_section_exits_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["disc", "sweep", str(EXAMPLES / "arc.toml"), "--out", str(tmp_path)])
    assert e.value.code == 2 and "disc run" in capsys.readouterr().err


def test_table_prints_rows(tmp_path, capsys):
    main(["disc", "table", str(EXAMPLES / "linear.toml"), "--rpm", "3000:5000:1000"])
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert lines[0] == disc_uncalibrated()
    assert lines[1].split()[0] == "rpm"
    assert [l.split()[0] for l in lines[2:5]] == ["3000", "4000", "5000"]
    assert "exit speed" in lines[-1] and "x wheel surface speed" in lines[-1]


def test_bad_setup_exits_2_with_message(tmp_path, capsys):
    p = tmp_path / "bad.toml"
    p.write_text((EXAMPLES / "linear.toml").read_text().replace("[goal]", "[goal]\ndistnce = 3.0"))
    with pytest.raises(SystemExit) as e:
        main(["disc", "run", str(p), "--out", str(tmp_path / "o")])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("scrimmage:") and "distnce" in err and "distance" in err


def test_bad_sweep_key_names_key(tmp_path, capsys):
    p = tmp_path / "bad.toml"
    p.write_text((EXAMPLES / "arc.toml").read_text() + '\n[sweep]\n"layout.compresion" = [0.01]\n')
    with pytest.raises(SystemExit) as e:
        main(["disc", "sweep", str(p), "--out", str(tmp_path / "o")])
    assert e.value.code == 2 and "compresion" in capsys.readouterr().err


def test_bad_table_range_exits_2(capsys):
    with pytest.raises(SystemExit) as e:
        main(["disc", "table", str(EXAMPLES / "linear.toml"), "--rpm", "5000:3000:1000"])
    assert e.value.code == 2 and "--rpm" in capsys.readouterr().err


def test_run_accepts_sweep_with_line_chart(tmp_path, capsys):
    pytest.importorskip("matplotlib")
    p = quick("linear.toml", tmp_path, '\n[[chart]]\nkind = "line"\nx = "launcher.tilt_deg"\ny = "exit_speed"\n')
    out = tmp_path / "o"
    main(["disc", "run", str(p), "--out", str(out)])  # run ignores [sweep]: the line chart is skipped, not an error
    assert (out / "results.csv").exists() and (out / "shaft.png").exists()
    assert not list(out.glob("line-*.png"))


@pytest.mark.parametrize("feed", ["rate = 0", "rate = -1", "count = 0", "lanes = [0.0]"])
def test_bad_feed_exits_2_with_message(tmp_path, capsys, feed):
    p = tmp_path / "bad.toml"
    text = (EXAMPLES / "linear.toml").read_text()
    assert "\n[feed]" in text or "[feed]" not in text
    p.write_text(text + f"\n[feed]\n{feed}\n" if "[feed]" not in text else text.replace("[feed]", f"[feed]\n{feed}", 1))
    with pytest.raises(SystemExit) as e:
        main(["disc", "run", str(p), "--out", str(tmp_path / "o")])
    assert e.value.code == 2
    assert capsys.readouterr().err.startswith("scrimmage:")
