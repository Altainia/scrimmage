"""The Bench's, the scenario reports' and the Measure tab's server-built words in the Units setting (spec U5):
headlines, summary.txt, chart axes, the scale bar, check messages, report.md and measure summaries, each in both
systems. Machine output (the CSVs, report.json, MCP results) doesn't change with the setting (Goal 5)."""
import csv
import dataclasses
import json
import re
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scrimmage import units as u
from scrimmage.bench.report import headlines, write_results
from scrimmage.bench.run import LoadStats, run_bench
from scrimmage.bench.spec import load_bench, parse_bench, with_overrides

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "bench"


@pytest.fixture(scope="module")
def pinch():
    return run_bench(load_bench(EXAMPLES / "pinch_car.toml"))


@pytest.fixture(scope="module")
def hooded_flight():
    path = EXAMPLES / "hooded.toml"
    raw = with_overrides(tomllib.loads(path.read_text()), {"inlet.feed.count": 2})
    raw["flight"] = {"outlet": "shot", "height": 0.5, "target_distance": 3.0, "target_height": 2.0,
                     "ring_diameter": 1.2}
    return run_bench(parse_bench(raw, path.parent))


def _speed(line: str, unit: str) -> float:
    return float(re.search(rf"at (\d+\.\d) {re.escape(unit)}", line).group(1))


# -------------------------------------------------------------------------------------------------- the bench

def test_a_headline_in_both_systems(pinch, units):
    imperial = headlines(pinch)[0]
    assert re.fullmatch(r"3 of 3 left through `shot` at \d+\.\d ft/s ± \d+\.\d, \d+° down", imperial), imperial
    units("metric")
    metric = headlines(pinch)[0]
    assert re.fullmatch(r"3 of 3 left through `shot` at \d+\.\d m/s ± \d+\.\d, \d+° down", metric), metric
    assert _speed(imperial, "ft/s") == pytest.approx(_speed(metric, "m/s") / u.FT, abs=0.2)


def test_a_load_headline_in_both_systems(pinch, units):
    hit = dataclasses.replace(pinch, loads={s: LoadStats(shape=s, peak_force_n=f, peak_impulse_ns=0.0, at=None)
                                            for s, f in {"top": 50.0, "bottom": 200.0}.items()})
    assert any("up to 45 lbf, 4.0 times a typical other part's (11 lbf)" in x for x in headlines(hit))
    units("metric")
    assert any("up to 200 N, 4.0 times a typical other part's (50 N)" in x for x in headlines(hit))


def test_a_flight_headline_in_both_systems(hooded_flight, units):
    assert any("ring 9.8 ft away, 6.6 ft up" in x for x in headlines(hooded_flight)), headlines(hooded_flight)
    units("metric")
    assert any("ring 3.0 m away, 2.0 m up" in x for x in headlines(hooded_flight)), headlines(hooded_flight)


def test_summary_follows_the_setting_and_the_csvs_stay_si(pinch, units, tmp_path):
    imperial = write_results(pinch, tmp_path / "imperial")
    units("metric")
    metric = write_results(pinch, tmp_path / "metric")
    for name in ("pieces.csv", "shafts.csv", "loads.csv"):
        assert (imperial / name).read_text() == (metric / name).read_text()
    assert "speed_m_s" in (metric / "pieces.csv").read_text().splitlines()[0]
    text_i, text_m = (imperial / "summary.txt").read_text(), (metric / "summary.txt").read_text()
    assert "speed_ft_s" in text_i and "peak_force_lbf" in text_i and "at_x_in" in text_i and "ft/s" in text_i
    assert "speed_m_s" in text_m and "peak_force_n" in text_m and "at_x_mm" in text_m and "m/s" in text_m
    speed = float(next(csv.DictReader((metric / "pieces.csv").open()))["speed_m_s"])
    row = next(line for line in text_i.splitlines() if line.strip().startswith("0 ")).split()
    assert float(row[7]) == pytest.approx(speed / u.FT, abs=0.002)   # from the run's own number, not the CSV's


def test_a_chart_axis_in_both_systems(units, tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    from matplotlib.axes import Axes

    from scrimmage.bench import compare
    from scrimmage.launcher import report
    said, plotted = [], []
    set_ylabel, plot = Axes.set_ylabel, Axes.plot
    monkeypatch.setattr(Axes, "set_ylabel",
                        lambda self, text, *a, **k: (said.append(text), set_ylabel(self, text, *a, **k))[1])
    monkeypatch.setattr(Axes, "plot", lambda self, xs, ys, *a, **k: (plotted.append(list(ys)),
                                                                      plot(self, xs, ys, *a, **k))[1])
    nan = float("nan")
    base = {k: nan for k in compare.BENCH_METRICS}
    rows = [({"wheel.top.diameter": d}, compare.Row({**base, "exit_speed": s, "peak_load_n": 100.0}))
            for d, s in ((0.08, 6.0), (0.1, 7.5))]
    charts = [{"kind": "line", "x": "wheel.top.diameter", "y": "exit_speed"},
              {"kind": "line", "x": "wheel.top.diameter", "y": "peak_load_n"},
              {"kind": "line", "x": "wheel.top.diameter", "y": "jammed"}]

    def chart(out):
        said.clear(), plotted.clear()
        report.write_charts(rows, charts, out, None, compare.BENCH_METRICS, compare.CHART_KINDS, footer="x",
                            shown=compare.SHOWN)
        return list(said), [list(p) for p in plotted]

    labels, values = chart(tmp_path / "i")
    assert labels == ["exit_speed (ft/s)", "peak_load_n (lbf)", "jammed"]
    assert values[0] == pytest.approx([6.0 / u.FT, 7.5 / u.FT])
    units("metric")
    labels, values = chart(tmp_path / "m")
    assert labels == ["exit_speed (m/s)", "peak_load_n (N)", "jammed"]
    assert values[0] == pytest.approx([6.0, 7.5]) and values[1] == pytest.approx([100.0, 100.0])


def test_the_scale_bar_in_both_systems(units):
    from scrimmage.bench.pictures import scale_bar
    assert scale_bar(0.25) == (pytest.approx(5 * u.IN), "5 in")
    assert scale_bar(1.0) == (pytest.approx(2 * u.FT), "2 ft")
    units("metric")
    assert scale_bar(0.25) == (pytest.approx(0.2), "20 cm")
    assert scale_bar(1.0) == (pytest.approx(1.0), "1 m")


def test_the_gallery_table_in_both_systems(pinch, units):
    from scrimmage.bench.gallery import Entry, _table_html
    rows = [(Entry(file=EXAMPLES / "pinch_car.toml", result=pinch), "pinch car", "a")]
    assert "exit speed (ft/s)" in _table_html(rows) and "hardest hit (lbf)" in _table_html(rows)
    units("metric")
    assert "exit speed (m/s)" in _table_html(rows) and "hardest hit (N)" in _table_html(rows)


def test_mcp_bench_headlines_are_in_meters_whatever_the_setting(pinch, hooded_flight, units):
    """The MCP tools' headlines are main's text exactly, in m/s, N and m, under either setting."""
    from scrimmage.mcp_server import _bench_ideas, _machine_headlines
    hit = dataclasses.replace(pinch, loads={s: LoadStats(shape=s, peak_force_n=f, peak_impulse_ns=0.0, at=None)
                                            for s, f in {"top": 50.0, "bottom": 200.0}.items()})
    for system in ("imperial", "metric"):
        units(system)
        lines = _machine_headlines(hit)
        assert re.fullmatch(r"3 of 3 left through `shot` at \d+\.\d m/s ± \d+\.\d, \d+° down", lines[0]), lines
        assert any(x.endswith("up to 200 N, 4.0 times a typical other part's (50 N)") for x in lines), lines
        assert any("ring 3.0 m away, 2.0 m up (they come down through its height " in x
                   for x in _machine_headlines(hooded_flight))
        assert _bench_ideas([hit], [EXAMPLES / "pinch_car.toml"])[0]["headlines"] == lines
    units("imperial")
    assert "ft/s" in headlines(pinch)[0]   # outside the MCP answer, the setting


# ----------------------------------------------------------------------------------------- scenario reports

class _World:
    def __init__(self, pose):
        self.pose = pose

    def robot_pose(self):
        return self.pose


def _pose_result():
    from scrimmage.checks import PoseCheck
    check = PoseCheck({"x": 3.81, "y": 1.0, "tol_xy": 0.1524}, "step 1")
    return check.end(SimpleNamespace(world=_World((3.81 + 0.3048, 1.0, 0.0))))


MAIN_POSE = ("pose: 0.30 m from target, heading off by 0.0° (x=4.11, y=1.00, heading=0.0°); allowed 0.1524 m, "
             "5°")   # main's text, which report.json and MCP results keep


def test_a_pose_check_message_in_feet_and_meters(units):
    r = _pose_result()
    assert r.shown() == ("pose: 1 ft from target, heading off by 0.0° (x=13.5 ft, y=3.281 ft, heading=0.0°); allowed "
                         "0.5 ft, 5°")
    units("metric")
    assert r.shown() == ("pose: 0.3048 m from target, heading off by 0.0° (x=4.1148 m, y=1 m, heading=0.0°); "
                         "allowed 0.1524 m, 5°")
    # the machine message is main's whatever the setting (report.json, the MCP result)
    assert r.message == MAIN_POSE and _pose_result().message == MAIN_POSE
    assert r.to_dict()["message"] == MAIN_POSE and r.to_dict(shown=True)["message"] == r.shown()
    assert "words" not in r.to_dict()


def test_an_odometry_check_message_in_both_systems(units):
    from scrimmage.checks import OdometryErrorCheck
    check = OdometryErrorCheck({"max": 0.1}, "step 1")
    link = SimpleNamespace(odometry=lambda: (1.0, 2.0, 0.0))
    r = check.end(SimpleNamespace(world=_World((1.0 + 0.0508, 2.0, 0.0)), bridge=SimpleNamespace(nt_link=link)))
    assert r.shown() == "odometry 0.167 ft from ground truth at the end; allowed 0.328 ft"
    units("metric")
    assert r.shown() == "odometry 0.0508 m from ground truth at the end; allowed 0.1 m"
    assert r.message == "odometry 0.051 m from ground truth at the end; allowed 0.1 m"   # main's text


def test_report_md_follows_the_setting_and_report_json_stays_fixed(units, tmp_path):
    from scrimmage.scenario_runner import Report, StepReport
    ball = {"id": 1, "fired_t": 1.0, "robot_pose": [3.0, 1.0, 90.0], "robot_velocity": [1.0, 0.0, 0.0],
            "hood_deg": 40.0, "shooter_rps": 50.0, "exit_speed": 9.0, "spin_rpm": 900.0, "exit_slip": 0.1,
            "outcome": "on_field", "at_rest": True, "last_pos": [6.0, 2.0, 0.08], "first_hit": None,
            "apex_z": 1.5, "rim_height": 1.83, "nearest_goal": "hub"}

    def report():
        rep = Report(scenario="s", description="", steps=[StepReport("drive", "auto", 0.0, 3.0, checks=[_pose_result()])],
                     range={"score": 0, "goals": {"hub": 0}, "fired": 1, "held": 0, "on_field": 1}, balls=[ball])
        return rep

    units("metric")
    report().write(tmp_path)
    md = (tmp_path / "report.md").read_text()
    assert "0.3048 m from target" in md and "| x m | y m |" in md and "exit m/s" in md
    assert "rest at (6.00, 2.00) m" in md and "apex 1.50 m vs rim 1.83 m (hub)" in md
    machine = json.loads((tmp_path / "report.json").read_text())["steps"][0]["checks"][0]["message"]
    assert machine == MAIN_POSE
    units("imperial")
    report().write(tmp_path)
    md = (tmp_path / "report.md").read_text()
    assert "1 ft from target" in md and "| x ft | y ft |" in md and "exit ft/s" in md
    assert "rest at (19.69, 6.56) ft" in md and "| 9.84 | 3.28 |" in md
    assert json.loads((tmp_path / "report.json").read_text())["steps"][0]["checks"][0]["message"] == machine


# --------------------------------------------------------------------------------------------------- measure

def test_measure_summaries_in_both_systems(units):
    from scrimmage.measure.cli import FIELD
    assert [FIELD[k][1](v) for k, v in (("mass", 0.142), ("diameter", 0.15), ("stiffness_n", 118.0))] == \
        ["0.313 lb", "5.906 in", "26.53 lbf"]
    units("metric")
    assert [FIELD[k][1](v) for k, v in (("mass", 0.142), ("diameter", 0.15), ("stiffness_n", 118.0))] == \
        ["0.1420 kg", "150.0 mm", "118.0 N"]
