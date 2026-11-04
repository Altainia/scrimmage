"""Labels that say which numbers are measured and which are still estimates (spec goal 5, C7): the three forms, a
measurement that isn't good, and each caller (the launcher's report and charts, the bench line, the Test tab's grip
and pneumatic notes, the air budget) with and without a measurement."""
from __future__ import annotations

import dataclasses
from pathlib import Path

from scrimmage.measure.file import Provenance
from scrimmage.measure.labels import LAUNCHER, common, line
from test_measure_use import ball_file, friction_file, shooter

FUEL = Provenance(file=Path("/team/fuel.measure.toml"), by="J. Smith", date="2026-11-10", trials=5, good=True)
TREAD = Provenance(file=Path("/team/tread.measure.toml"), by="A. Lee", date="2026-11-12", trials=4, good=True)
SHORT = Provenance(file=Path("/team/fuel.measure.toml"), by="J. Smith", date="2026-11-10", trials=2, good=False,
                   why="2 trials: take at least 3")


# ------------------------------------------------------------------------------------------------ the forms

def test_nothing_measured_is_the_old_wording_with_what_matters():
    assert line({}, LAUNCHER) == ("UNCALIBRATED: estimates from preset material numbers, not fitted to measurements "
                                  "(ball stiffness, bounce, wheel friction)")
    assert line({"ball.stiffness_n": None}, LAUNCHER) == line({}, LAUNCHER)


def test_some_measured_names_the_estimates_and_the_file():
    got = line({"ball.stiffness_n": FUEL, "ball.restitution": FUEL}, LAUNCHER)
    assert got == ("UNCALIBRATED: still estimates: wheel friction. Measured: ball stiffness and bounce "
                   "(fuel.measure.toml, J. Smith, 2026-11-10, 5 trials)")


def test_all_measured_and_good_is_calibrated():
    got = line({"ball.stiffness_n": FUEL, "ball.restitution": FUEL, "wheels.friction": TREAD}, LAUNCHER)
    assert got == ("Calibrated from measurements: ball stiffness and bounce (fuel.measure.toml, J. Smith, 2026-11-10, "
                   "5 trials); wheel friction (tread.measure.toml, A. Lee, 2026-11-12, 4 trials)")
    assert "UNCALIBRATED" not in got


def test_a_measurement_without_who_or_when_names_its_file_and_trials():
    anon = dataclasses.replace(FUEL, by="", date="")
    got = line({"ball.stiffness_n": anon, "ball.restitution": anon, "wheels.friction": TREAD}, LAUNCHER)
    assert got.startswith("Calibrated from measurements: ball stiffness and bounce (fuel.measure.toml, 5 trials); ")
    dated = dataclasses.replace(FUEL, by="")
    assert "(fuel.measure.toml, 2026-11-10, 5 trials)" in line({"ball.stiffness_n": dated}, LAUNCHER)


def test_a_measurement_that_is_not_good_is_an_estimate_and_says_why():
    got = line({"ball.stiffness_n": SHORT, "ball.restitution": FUEL, "wheels.friction": TREAD}, LAUNCHER)
    assert got.startswith("UNCALIBRATED: still estimates: ball stiffness (fuel.measure.toml, not good enough: 2 "
                          "trials; take at least 3). Measured: ball bounce (fuel.measure.toml")
    alone = line({"ball.stiffness_n": SHORT}, LAUNCHER)
    assert alone == ("UNCALIBRATED: still estimates: ball stiffness (fuel.measure.toml, not good enough: 2 trials; "
                     "take at least 3), ball bounce, wheel friction")


def test_runs_share_only_the_provenance_every_one_has():
    assert common([{"ball.stiffness_n": FUEL, "wheels.friction": TREAD}, {"ball.stiffness_n": FUEL}]) == {
        "ball.stiffness_n": FUEL}
    assert common([]) == {}


# ---------------------------------------------------------------------------------------------- the launcher

def test_the_launcher_run_report_without_a_measurement(tmp_path, capsys):
    from scrimmage.__main__ import main
    from scrimmage.launcher.report import UNCALIBRATED
    assert UNCALIBRATED == ("UNCALIBRATED: estimates from preset material numbers, not fitted to measurements "
                            "(ball stiffness, bounce, wheel friction).")
    main(["launcher", "run", str(shooter(tmp_path)), "--out", str(tmp_path / "out")])
    assert capsys.readouterr().out.splitlines()[0] == UNCALIBRATED


def test_the_launcher_run_report_with_a_measurement(tmp_path, capsys, monkeypatch):
    from matplotlib.figure import Figure

    from scrimmage.__main__ import main
    footers = []
    real = Figure.text
    monkeypatch.setattr(Figure, "text", lambda self, x, y, s, *a, **k: (footers.append(s), real(self, x, y, s, *a,
                                                                                                    **k))[1])
    path = shooter(tmp_path, ball=f'measured = "{ball_file(tmp_path)}"\n')
    main(["launcher", "run", str(path), "--out", str(tmp_path / "out")])
    first = capsys.readouterr().out.splitlines()[0]
    assert first == ("UNCALIBRATED: still estimates: wheel friction. Measured: ball stiffness and bounce "
                     "(fuel.measure.toml, J. Smith, 2026-11-10, 3 trials).")
    assert footers and {f.replace("\n", " ") for f in footers} == {first}  # each chart's footer: the line, wrapped

    both = shooter(tmp_path, ball=f'measured = "{ball_file(tmp_path)}"\n',
                   wheels=f'measured = "{friction_file(tmp_path)}"\n', name="both.toml")
    main(["launcher", "run", str(both), "--out", str(tmp_path / "out2")])
    assert capsys.readouterr().out.splitlines()[0] == (
        "Calibrated from measurements: ball stiffness and bounce (fuel.measure.toml, J. Smith, 2026-11-10, 3 trials); "
        "wheel friction (tread.measure.toml, J. Smith, 2026-11-10, 3 trials).")


def test_a_swept_number_reads_as_an_estimate(tmp_path):
    from scrimmage.launcher import report, sweep
    path = shooter(tmp_path, ball=f'measured = "{ball_file(tmp_path)}"\n')
    path.write_text(path.read_text(encoding="utf-8") + '\n[sweep]\n"ball.stiffness_n" = [80.0, 120.0]\n',
                    encoding="utf-8")
    _, setups = sweep.run_all(path)
    assert report.calibration(setups).startswith("UNCALIBRATED: still estimates: ball stiffness, wheel friction. "
                                                 "Measured: ball bounce (fuel.measure.toml")


def test_the_aiming_table_says_what_its_setup_measured():
    from scrimmage.testrange import aim
    from scrimmage.launcher.report import UNCALIBRATED, calibration
    assert f"// {UNCALIBRATED}" in aim.to_java([], "Aim", "cmd")
    setup = type("S", (), {"provenance": {"ball.stiffness_n": FUEL}})()
    label = calibration([setup])
    assert label.startswith("UNCALIBRATED: still estimates: ball bounce, wheel friction. Measured: ball stiffness")
    assert f"// {label}" in aim.to_java([], "Aim", "cmd", label=label)


def test_the_disc_report_names_what_matters():
    from scrimmage.disc.report import DISC_UNCALIBRATED
    assert DISC_UNCALIBRATED == ("UNCALIBRATED: estimates from preset material numbers, not fitted to measurements "
                                 "(disc inertia, rim stiffness, tread friction).")


# ------------------------------------------------------------------------------------------------- the bench

def test_the_bench_line_names_its_piece_stiffness_and_friction():
    from scrimmage.bench.report import bench_uncalibrated
    squishy = type("B", (), {"piece": type("P", (), {"kind": "squishy"})()})()
    rigid = type("B", (), {"piece": type("P", (), {"kind": "rigid"})()})()
    assert bench_uncalibrated(squishy) == (
        "UNCALIBRATED: estimates from preset numbers, not measurements (ball stiffness, tread friction); measure the "
        "ball's stiffness (a squeeze test) and its bounce (a drop test) first; see docs/launcher.md, \"Measure these "
        "first\".")
    assert bench_uncalibrated(rigid).startswith(
        "UNCALIBRATED: estimates from preset numbers, not measurements (piece stiffness, tread friction); measure the "
        "tread's friction")


def test_the_bench_pictures_say_what_matters():
    from scrimmage.bench.pictures import picture_uncalibrated
    squishy = type("B", (), {"piece": type("P", (), {"kind": "squishy"})()})()
    assert picture_uncalibrated(squishy) == ("UNCALIBRATED: estimates from preset numbers, not measurements (ball "
                                             "stiffness, tread friction)")


# ----------------------------------------------------------------------------------- the Test tab and air

class _Parts:
    def __init__(self, cylinders):
        self.instances, self.cylinders, self.range = [], cylinders, None


def _cylinder(name, provenance=None):
    from scrimmage.pneumatics.model import CylinderSpec
    return CylinderSpec(name, bore=0.0254, rod=0.0079375, stroke=0.1524, friction=0.0, conductance=8e-9,
                        provenance=provenance)


def test_the_pneumatic_note_without_and_with_a_measurement():
    from scrimmage.blocks.run import pneumatic_note
    assert pneumatic_note([_cylinder("lift"), _cylinder("grab")]) == (
        "UNCALIBRATED: pneumatic numbers are estimates (lift valve conductance, grab valve conductance); see "
        "docs/pneumatics.md.")
    lift = dataclasses.replace(FUEL, file=Path("lift.measure.toml"))
    assert pneumatic_note([_cylinder("lift", lift), _cylinder("grab")]) == (
        "UNCALIBRATED: still estimates: grab valve conductance. Measured: lift valve conductance (lift.measure.toml, "
        "J. Smith, 2026-11-10, 5 trials); see docs/pneumatics.md.")
    assert pneumatic_note([_cylinder("lift", lift)]).startswith("Calibrated from measurements: lift valve conductance")


def test_run_notes_take_the_cylinders_from_the_world():
    from scrimmage.blocks.run import run_notes
    lift = dataclasses.replace(FUEL, file=Path("lift.measure.toml"))
    world = type("W", (), {"parts": _Parts([_cylinder("lift", lift)]), "pneumatics": object()})()
    program = type("P", (), {"blocks": [], "chart": [], "path": "t.test.toml"})()
    notes = run_notes(world, program)
    assert notes == ["Calibrated from measurements: lift valve conductance (lift.measure.toml, J. Smith, 2026-11-10, "
                     "5 trials); see docs/pneumatics.md."]


def test_run_notes_name_a_measured_piece_friction(tmp_path):
    from scrimmage.blocks.run import run_notes
    from scrimmage.testrange.spec import load_range
    from test_measure_use import friction_file, range_file
    name = friction_file(tmp_path / "m")
    range_file(tmp_path, pieces='\n[piece.crate]\nshape = "box"\nsize = [0.2, 0.2, 0.2]\nmass = 1.0\n'
                                f'friction = "m/{name}"\n\n[piece.cone]\nshape = "box"\nsize = [0.2, 0.2, 0.2]\n'
                                'mass = 1.0\nfriction = 0.5\n')
    rng = load_range("mine.toml", tmp_path)
    world = type("W", (), {"test_range": type("T", (), {"range": rng})(), "parts": None})()
    program = type("P", (), {"blocks": [], "chart": [], "path": "t.test.toml"})()
    assert run_notes(world, program) == ["crate piece: measured, friction 0.8 (tread.measure.toml, J. Smith, "
                                         "2026-11-10, 3 trials)"]


def test_the_grip_note_without_and_with_a_measurement(tmp_path):
    from scrimmage.parts.grip import grip_notes
    from scrimmage.parts.model import load_part
    from test_measure_use import claw

    def note(folder):
        part = load_part(folder)
        world = type("W", (), {"parts": type("P", (), {"instances": [type("I", (), {"name": "claw",
                                                                                   "part": part})()]})()})()
        (n,) = grip_notes(world)
        return n

    assert note(claw(tmp_path / "plain", '[body.pad]\ngrip = "nitrile"\n')) == (
        "claw.pad grip: nitrile, friction 0.8 (UNCALIBRATED estimate; measure yours with a sled pull, "
        "docs/test-tab.md)")
    good = claw(tmp_path / "good", '[body.pad]\ngrip = "pad.measure.toml"\n')
    friction_file(good, name="pad.measure.toml")
    assert note(good) == "claw.pad grip: measured, friction 0.8 (pad.measure.toml, J. Smith, 2026-11-10, 3 trials)"
    short = claw(tmp_path / "short", '[body.pad]\ngrip = "pad.measure.toml"\n')
    friction_file(short, pulls=(4.0, 4.0), name="pad.measure.toml")
    assert note(short) == ("claw.pad grip: measured, friction 0.8 (pad.measure.toml, J. Smith, 2026-11-10, 2 trials), "
                           "not good enough: 2 trials; take at least 3 (UNCALIBRATED)")


def test_the_air_budget_names_what_it_rests_on_and_never_reads_calibrated():
    """A budget counts air, not how fast it flows: a measured valve doesn't calibrate it."""
    from scrimmage.pneumatics.budget import budget, format_budget
    from scrimmage.pneumatics.model import AirConfig
    plain = format_budget(budget(AirConfig(), [_cylinder("cyl")]))
    assert plain.splitlines()[-1] == ("UNCALIBRATED: estimates (compressor flow, pressure switch points, tank volume, "
                                      "dead volume); see docs/pneumatics.md.")
    assert format_budget(budget(AirConfig(), [_cylinder("cyl", TREAD)])) == plain


# ------------------------------------------------------------------------- footers and a measured range ball

def _footers(monkeypatch) -> list[str]:
    from matplotlib.figure import Figure
    seen = []
    real = Figure.text
    monkeypatch.setattr(Figure, "text", lambda self, x, y, s, *a, **k: (seen.append(s), real(self, x, y, s, *a,
                                                                                                 **k))[1])
    return seen


def test_a_bench_sweep_chart_carries_the_bench_line(tmp_path, monkeypatch, capsys):
    from scrimmage.__main__ import main
    from scrimmage.bench.report import bench_estimates
    from scrimmage.bench.spec import load_bench
    from test_bench_gallery import _one_car, _write
    footers = _footers(monkeypatch)
    f = _write(tmp_path, "car.toml", _one_car() + '\n[sweep]\n"shaft.top.target_rps" = [30, 45]\n')
    main(["bench", "sweep", str(f)])
    capsys.readouterr()
    want = bench_estimates(load_bench(f))
    assert want.startswith("UNCALIBRATED: estimates from preset numbers, not measurements (")
    assert footers and set(footers) == {want}


def test_a_no_measurement_chart_footer_stays_on_one_line(tmp_path, monkeypatch):
    from scrimmage.disc.report import DISC_UNCALIBRATED
    from scrimmage.launcher import report, sweep
    footers = _footers(monkeypatch)
    rows, _ = sweep.run_one(shooter(tmp_path))
    for line_ in (report.UNCALIBRATED, DISC_UNCALIBRATED):
        assert len(line_) <= report.FOOTER_WIDTH
        report.write_charts(rows, [{"kind": "shaft"}], tmp_path / "out", footer=line_)
    assert footers == [report.UNCALIBRATED, DISC_UNCALIBRATED]  # unwrapped


def test_a_long_chart_footer_wraps(tmp_path, monkeypatch):
    from scrimmage.launcher import report, sweep
    footers = _footers(monkeypatch)
    rows, setups = sweep.run_one(shooter(tmp_path))
    long = report.calibration([type("S", (), {"provenance": {"ball.stiffness_n": FUEL, "ball.restitution": SHORT,
                                                             "wheels.friction": TREAD}})()])
    assert len(long) > report.FOOTER_WIDTH
    (png,) = report.write_charts(rows, [{"kind": "shaft"}], tmp_path / "out", footer=long)
    (text,) = footers
    assert all(len(ln) <= report.FOOTER_WIDTH for ln in text.split("\n")) and text.replace("\n", " ") == long
    from PIL import Image
    with Image.open(png) as im:
        assert im.width < 1000            # 7 x 100 dpi, not as wide as the line


def test_the_aiming_line_names_a_measured_range_ball(tmp_path):
    import tomlkit

    from range_fixtures import shooter_cfg
    from scrimmage.testrange import aim
    name = ball_file(tmp_path)
    cfg = tmp_path / "robot.toml"
    cfg.write_text(tomlkit.dumps(shooter_cfg(launcher={"hood_gap": 0.10}, range={"ball": name})), encoding="utf-8")
    model = aim.load_model(cfg, "hub_blue", 10.0)
    assert {"ball.stiffness_n", "ball.restitution"} <= set(model.setup.provenance)
    assert aim.calibration(model) == ("UNCALIBRATED: still estimates: wheel friction. Measured: ball stiffness and "
                                      "bounce (fuel.measure.toml, J. Smith, 2026-11-10, 3 trials).")
