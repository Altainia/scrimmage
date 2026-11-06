import builtins
import sys

import pytest

from scrimmage.__main__ import main
from scrimmage.launcher.report import uncalibrated
from scrimmage.launcher.run import METRIC_KEYS
from scrimmage.launcher.sweep import combinations

SETUP = """
[ball]
preset = "2020-power-cell"
[wheels]
diameter = 0.1016
width = 0.05
preset = "30A"
positions = [0.0]
[shaft]
wheel_inertia = 0.002
[control]
target_rps = 60.0
kP = 0.2
[hood]
gap = 0.14224
exit_angle_deg = 40.0
friction = 0.5
[launcher]
height = 0.5
width = 0.3
[goal]
distance = 3.0
height = 2.64
diameter = 1.2
[feed]
count = 3
rate = 5.0
rule = "schedule"
"""

SWEEP = SETUP + """
[sweep]
"shaft.added_inertia" = [0.0, 0.004]
"hood.exit_angle_deg" = [35.0, 45.0]
"""


def write(tmp_path, text, name="rig.toml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_combinations_product_in_order():
    combos = combinations({"a": [1, 2], "b": ["x", "y", "z"]})
    assert len(combos) == 6
    assert combos[0] == {"a": 1, "b": "x"} and combos[1] == {"a": 1, "b": "y"}
    assert combos[3] == {"a": 2, "b": "x"} and list(combos[0]) == ["a", "b"]
    assert combinations({}) == [{}]


def test_sweep_typo_names_key(tmp_path, capsys):
    p = write(tmp_path, SETUP + '[sweep]\n"hood.gpa" = [0.1, 0.2]\n')
    with pytest.raises(SystemExit) as e:
        main(["launcher", "sweep", str(p)])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "gpa" in err and "gap" in err and err.startswith("scrimmage:")


def test_run_prints_uncalibrated_first(tmp_path, capsys):
    p = write(tmp_path, SWEEP)  # run ignores [sweep]
    main(["launcher", "run", str(p), "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert out.splitlines()[0] == uncalibrated()
    assert uncalibrated() == ("UNCALIBRATED: estimates from preset material numbers, not fitted to measurements "
                            "(ball stiffness, bounce, wheel friction).")
    assert "exit_speed" in out and "shaft.added_inertia" not in out
    assert (tmp_path / "o" / "results.csv").exists()


def test_default_out_dir(tmp_path, capsys):
    p = write(tmp_path, SETUP)
    main(["launcher", "run", str(p)])
    assert (tmp_path / "rig-results" / "results.csv").exists()


def test_csv_columns_and_repeatable(tmp_path, capsys):
    p = write(tmp_path, SWEEP)
    a, b = tmp_path / "a", tmp_path / "b"
    main(["launcher", "sweep", str(p), "--out", str(a)])
    main(["launcher", "sweep", str(p), "--out", str(b)])
    ca, cb = (a / "results.csv").read_bytes(), (b / "results.csv").read_bytes()
    assert ca == cb
    lines = ca.decode().splitlines()
    assert lines[0].split(",") == ["shaft.added_inertia", "hood.exit_angle_deg", *METRIC_KEYS]
    assert len(lines) == 5
    out = capsys.readouterr().out
    assert out.splitlines()[0] == uncalibrated()


def test_charts_written(tmp_path, capsys):
    pytest.importorskip("matplotlib")
    p = write(tmp_path, SWEEP)
    main(["launcher", "sweep", str(p), "--out", str(tmp_path / "o")])
    names = sorted(f.name for f in (tmp_path / "o").glob("*.png"))
    assert len(names) == 3 and any("shaft" in n for n in names) and any("landing" in n for n in names)
    assert all((tmp_path / "o" / n).stat().st_size > 1000 for n in names)


def test_custom_chart_bad_key_exits_2(tmp_path, capsys):
    pytest.importorskip("matplotlib")
    p = write(tmp_path, SWEEP + '[[chart]]\nkind = "line"\nx = "hood.gap"\ny = "exit_speed"\n')
    with pytest.raises(SystemExit) as e:
        main(["launcher", "sweep", str(p), "--out", str(tmp_path / "o")])
    assert e.value.code == 2 and "hood.gap" in capsys.readouterr().err


def test_no_matplotlib_message(tmp_path, capsys, monkeypatch):
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.split(".")[0] == "matplotlib":
            raise ImportError("no matplotlib")
        return real(name, *a, **k)

    for m in [m for m in sys.modules if m.split(".")[0] == "matplotlib"]:
        monkeypatch.delitem(sys.modules, m)
    monkeypatch.setattr(builtins, "__import__", fake)
    p = write(tmp_path, SWEEP)
    main(["launcher", "sweep", str(p), "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert "charts need matplotlib: pip install matplotlib" in out
    assert (tmp_path / "o" / "results.csv").exists() and not list((tmp_path / "o").glob("*.png"))


def test_sweep_without_section_exits_2(tmp_path, capsys):
    p = write(tmp_path, SETUP)
    with pytest.raises(SystemExit) as e:
        main(["launcher", "sweep", str(p)])
    assert e.value.code == 2
    assert "no [sweep] section: use 'scrimmage launcher run'" in capsys.readouterr().err


def test_sweeping_list_valued_inputs_charts(tmp_path, capsys):
    # robot.velocity and feed.lanes take lists: the line chart groups and orders them without hashing them.
    pytest.importorskip("matplotlib")
    p = write(tmp_path, SETUP.replace("count = 3", "count = 2") + (
        '[sweep]\n"robot.velocity" = [[0.0, 0.0], [1.0, 0.0], [0.0, 0.0]]\n'
        '"feed.lanes" = [[0.0], [-0.03, 0.03]]\n'
    ))
    main(["launcher", "sweep", str(p), "--out", str(tmp_path / "o")])
    out = capsys.readouterr().out
    assert "[1, 0]" in out
    names = sorted(f.name for f in (tmp_path / "o").glob("*.png"))
    assert any(n.startswith("line-exit_speed-vs-robot.velocity") for n in names), names


def test_bad_sweep_combination_exits_2_before_running(tmp_path, capsys):
    p = write(tmp_path, SETUP + '[sweep]\n"hood.exit_angle_deg" = [40.0, 160.0]\n')
    with pytest.raises(SystemExit) as e:
        main(["launcher", "sweep", str(p), "--out", str(tmp_path / "o")])
    assert e.value.code == 2
    cap = capsys.readouterr()
    assert cap.err.startswith("scrimmage: hood.exit_angle_deg=160: ") and "wrap" in cap.err
    assert cap.out == "" and not (tmp_path / "o" / "results.csv").exists()


@pytest.mark.parametrize("chart", [
    '[[chart]]\nkind = "pie"\n',
    '[[chart]]\nkind = "line"\nx = "shaft.added_inertia"\ny = "exit_sped"\n',
    '[[chart]]\nkind = "line"\nx = "hood.gap"\ny = "exit_speed"\n',
    '[[chart]]\nkind = "line"\nx = "shaft.added_inertia"\ny = "exit_speed"\nseries = "hood.gap"\n',
], ids=["kind", "y", "x", "series"])
def test_bad_chart_spec_exits_2_before_running(tmp_path, capsys, chart):
    p = write(tmp_path, SWEEP + chart)
    with pytest.raises(SystemExit) as e:
        main(["launcher", "sweep", str(p), "--out", str(tmp_path / "o")])
    assert e.value.code == 2
    cap = capsys.readouterr()
    assert cap.err.startswith("scrimmage: [[chart]]") and cap.out == ""
    assert not (tmp_path / "o" / "results.csv").exists()


def test_run_skips_line_charts_of_swept_keys(tmp_path, capsys):
    # `run` ignores [sweep], so a line chart over a swept key has nothing to draw: it is skipped, not an error.
    pytest.importorskip("matplotlib")
    p = write(tmp_path, SWEEP + '[[chart]]\nkind = "line"\nx = "hood.exit_angle_deg"\ny = "exit_speed"\n'
                                '[[chart]]\nkind = "shaft"\n')
    main(["launcher", "run", str(p), "--out", str(tmp_path / "o")])
    assert [f.name for f in (tmp_path / "o").glob("*.png")] == ["shaft.png"]


def _table(out: str) -> list[list[str]]:
    """The printed table's header and rows (after the UNCALIBRATED line), split on runs of spaces."""
    import re
    lines = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("wrote")][1:]
    return [re.split(r"\s{2,}", ln.strip()) for ln in lines]


def test_the_printed_table_follows_the_units_but_results_csv_stays_si(tmp_path, capsys, units):
    """Cleanup A3: the table `launcher run` prints shows speeds and the landing spread in the Units setting (or
    `--units`), the unit in the header; results.csv keeps SI and its column names."""
    p = write(tmp_path, SETUP)
    main(["launcher", "run", str(p), "--out", str(tmp_path / "i")])
    imperial = _table(capsys.readouterr().out)
    main(["--units", "metric", "launcher", "run", str(p), "--out", str(tmp_path / "m")])
    metric = _table(capsys.readouterr().out)
    assert "exit_speed (ft/s)" in imperial[0] and "spread_downrange_m (ft)" in imperial[0]
    assert "exit_speed (m/s)" in metric[0] and "spread_lateral_m (m)" in metric[0]
    k = metric[0].index("exit_speed (m/s)")
    assert float(imperial[1][k]) == pytest.approx(float(metric[1][k]) / 0.3048, rel=1e-3)
    assert metric[0][metric[0].index("fired")] == "fired"                # counts are as they were
    assert (tmp_path / "i" / "results.csv").read_bytes() == (tmp_path / "m" / "results.csv").read_bytes()
    assert (tmp_path / "i" / "results.csv").read_text().splitlines()[0].split(",") == list(METRIC_KEYS)


def test_the_landing_chart_is_in_the_units_setting(tmp_path, capsys, monkeypatch):
    """Cleanup A3: the landing chart's axes are in the setting's field length, like the bench charts' units."""
    pytest.importorskip("matplotlib")
    from matplotlib.axes import Axes
    labels = []
    monkeypatch.setattr(Axes, "set_xlabel", lambda self, text, *a, **k: labels.append(text))
    p = write(tmp_path, SETUP)
    main(["launcher", "run", str(p), "--out", str(tmp_path / "i")])
    main(["--units", "metric", "launcher", "run", str(p), "--out", str(tmp_path / "m")])
    assert "downrange (ft)" in labels and "downrange (m)" in labels
