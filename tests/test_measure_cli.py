"""`scrimmage measure`: reading a file (exit 0 good, 1 not good, 2 bad file), printing a form, starting a file."""
import pytest

from scrimmage.__main__ import main
from scrimmage.measure import forms
from scrimmage.measure.fit_cylinder import Stroke, stroke_time
from scrimmage.pneumatics.model import DEFAULT_CONDUCTANCE

from measure_helpers import WITH_BALL, ball_file, frames, measure_file, synthetic

HEADER = '[measure]\nkind = "ball"\nabout = "a FUEL ball"\nby = "J. Smith"\ndate = 2026-11-10\nbase = "2026-fuel"\n'


def _run(*argv) -> int:
    with pytest.raises(SystemExit) as e:
        main(["measure", *map(str, argv)])
    return e.value.code


def _ball(tmp_path, body: str, name="fuel.measure.toml"):
    p = tmp_path / name
    p.write_text(HEADER + body, encoding="utf-8")
    return p


def _squeezes(*forces_lb) -> str:
    return "".join(f"\n[[squeeze]]\nforce_lb = {f}\n" for f in forces_lb)


# ------------------------------------------------------------------------------------------------- reading a file

def test_a_good_file_prints_each_trial_the_summary_and_exits_0(tmp_path, capsys, units):
    units("metric")   # the wording in newtons; tests/test_display_units_bench.py has both systems
    p = _ball(tmp_path, _squeezes(9.8, 9.6, 9.9, 9.7, 9.8))
    assert _run(p) == 0
    out = capsys.readouterr().out
    assert "a ball measurement: a FUEL ball" in out and "measured by J. Smith on 2026-11-10" in out
    assert "squeeze: gives stiffness_n" in out
    lines = out.splitlines()
    head = next(ln for ln in lines if ln.strip().startswith("trial"))
    assert head.split() == ["trial", "force_lb", "stiffness_n"]
    assert any(ln.split()[:3] == ["1", "9.8", "43.6"] for ln in lines)  # 9.8 lb is 43.6 N
    assert "stiffness_n: average 43.4 N, lowest 42.7 N, highest 44.0 N, spread 3% (5 trials)" in out
    assert "warning" not in out
    assert out.rstrip().endswith("Good enough to use: a run that names this file shows these numbers as measured.")


def test_two_trials_of_very_different_size_are_averaged_and_not_good(tmp_path, capsys, units):
    units("metric")   # the wording in newtons; tests/test_display_units_bench.py has both systems
    p = _ball(tmp_path, _squeezes(5, 15))
    assert _run(p) == 1
    out = capsys.readouterr().out
    assert "average 44.5 N" in out and "(2 trials)" in out
    assert "warning: take at least 3 trials (this has 2)" in out
    assert "warning: your trials disagree by 100%" in out
    assert "not good enough yet: stiffness_n (2 trials: take at least 3; trials disagree by 100%)" in out
    assert "A run that names this file uses it, but its result stays UNCALIBRATED until then." in out


def test_a_trial_that_looks_off_is_flagged(tmp_path, capsys):
    assert _run(_ball(tmp_path, _squeezes(9.8, 9.6, 9.9, 9.7, 12.5))) == 1
    assert "warning: trial 5 looks off: check it" in capsys.readouterr().out


def test_a_size_test_lines_up_each_trials_mass_and_diameter(tmp_path, capsys, units):
    units("metric")   # the wording in newtons; tests/test_display_units_bench.py has both systems
    body = ("\n[[size]]\nmass_g = 215\ncircumference_in = 18.5\n\n[[size]]\nmass_g = 217\n"
            "\n[[size]]\nmass_g = 216\ndiameter_mm = 150\n\n[[size]]\ncircumference_in = 18.6\n")
    assert _run(_ball(tmp_path, body)) == 0
    out = capsys.readouterr().out
    rows = [ln.split() for ln in out.splitlines() if ln.strip()[:1].isdigit()]
    assert rows[1] == ["2", "217", "0.2170", "kg"]  # a mass only: no diameter
    assert rows[3][:2] == ["4", "18.6"] and rows[3][-2:] == ["150.4", "mm"]
    assert "mass: average 0.2160 kg" in out and "diameter: average" in out and "(3 trials)" in out


def test_a_size_trial_that_looks_off_is_named_by_its_own_number(tmp_path, capsys):
    """A size test whose trial 2 only weighs: the diameter far off in trial 7 is called trial 7, not trial 6."""
    tapes = [18.5, None, 18.6, 18.4, 18.5, 18.6, 24.0]
    body = "".join(f"\n[[size]]\nmass_g = 216\n" + (f"circumference_in = {c}\n" if c else "") for c in tapes)
    assert _run(_ball(tmp_path, body)) == 1
    out = capsys.readouterr().out
    assert "warning: trial 7 looks off: check it" in out and "trial 6 looks off" not in out


def test_a_file_without_who_or_when_warns_but_is_judged_on_its_trials(tmp_path, capsys):
    p = tmp_path / "fuel.measure.toml"
    p.write_text('[measure]\nkind = "ball"\n' + _squeezes(9.8, 9.6, 9.9), encoding="utf-8")
    assert _run(p) == 0
    out = capsys.readouterr().out
    assert "measured (who and when not given)" in out
    assert 'warning: add who measured it and the date to [measure] (by = "J. Smith", date = 2026-11-10)' in out
    p.write_text('[measure]\nkind = "ball"\nby = "J. Smith"\n' + _squeezes(9.8, 9.6, 9.9), encoding="utf-8")
    assert _run(p) == 0
    out = capsys.readouterr().out
    assert "measured by J. Smith with" not in out and "measured by J. Smith\n" in out
    assert "warning: add the date to [measure] (date = 2026-11-10): the labels name them" in out


def test_every_test_of_a_ball_file_is_reported(tmp_path, capsys):
    body = (_squeezes(9.8, 9.6, 9.9) + '\n[drop_setup]\ndrop_in = 18\nfloor = "carpet"\n'
            + "".join(f"\n[[drop]]\nbounce_in = {b}\n" for b in (6.5, 6.6, 6.4)))
    assert _run(_ball(tmp_path, body)) == 0  # a carpet warns, but the average is good
    out = capsys.readouterr().out
    assert "squeeze: gives" in out and "drop: gives restitution, the bounce" in out
    assert "a ball bounces lower on a soft floor (carpet)" in out


def test_a_file_with_no_trials_is_not_good(tmp_path, capsys):
    assert _run(_ball(tmp_path, "")) == 1
    out = capsys.readouterr().out
    assert "no trials yet: write each trial as a [[size]] or [[squeeze]] or [[drop]] table" in out


@pytest.mark.parametrize("body, message", [
    ("\n[[squeeze]]\nforce = 9.8\n", "force needs its unit in its key"),
    ("\n[[squeeze]]\nforce_lbs = 9.8\n", "'lbs' isn't a unit of force"),
    ("\n[[sled]]\npull_lb = 4\n", "is a friction test"),
])
def test_a_bad_file_exits_2_with_the_error(tmp_path, capsys, body, message):
    assert _run(_ball(tmp_path, body)) == 2
    err = capsys.readouterr().err
    assert err.startswith("scrimmage: ") and message in err and "fuel.measure.toml" in err


def test_a_missing_file_exits_2(tmp_path, capsys):
    assert _run(tmp_path / "gone.measure.toml") == 2
    assert "gone.measure.toml: no such file" in capsys.readouterr().err


def test_a_file_that_isnt_text_exits_2(tmp_path, capsys):
    p = tmp_path / "photo.measure.toml"
    p.write_bytes(b"\xff\xd8\xff\xe0 a photo")
    assert _run(p) == 2
    assert "photo.measure.toml isn't a text file" in capsys.readouterr().err


def test_a_folder_exits_2(tmp_path, capsys):
    assert _run(tmp_path) == 2
    assert capsys.readouterr().err.startswith("scrimmage: ")


# ---------------------------------------------------------------------------------------------------------- fits

def test_a_good_launcher_fit_prints_each_shot_against_the_model_and_its_band(tmp_path, capsys, units):
    units("metric")   # the wording in newtons; tests/test_display_units_bench.py has both systems
    mf = synthetic(tmp_path, 120.0, 0.95, [50.0, 80.0])
    assert _run(mf.path) == 0
    out = capsys.readouterr().out
    assert "shot: fits the ball's stiffness to your shots (the wheel friction held at 0.95)" in out
    assert "speed across" in out and "model off by" in out
    assert "fit: stiffness 12" in out and "N, anywhere from " in out and "N fits your shots" in out
    assert "the model misses your shots by" in out
    assert "warning: Measure the ball's squeeze (the squeeze test) to fit wheel friction too." in out
    assert "good enough to use" in out


def test_a_launcher_fit_with_the_squeeze_measured_fits_the_wheel_friction(tmp_path, capsys, units):
    units("metric")   # the wording in newtons; tests/test_display_units_bench.py has both systems
    ball_file(tmp_path, [118.0, 120.0, 122.0])
    mf = synthetic(tmp_path, 120.0, 1.1, [50.0, 80.0], header=WITH_BALL)
    assert _run(mf.path) == 0
    out = capsys.readouterr().out
    assert "fits the wheel's friction to your shots (the ball's stiffness held at 120 N, from its squeeze test)" in out
    assert "fit: wheel friction 1.1" in out


def test_a_launcher_fit_at_the_edge_says_why_and_exits_1(tmp_path, capsys):
    mf = measure_file(tmp_path, [(50.0, frames(1.5)), (80.0, frames(2.0))])
    assert _run(mf.path) == 1
    out = capsys.readouterr().out
    assert "is at the edge of what it tries: check your shots" in out
    assert "not good enough yet: the fit is at the edge of what it tries" in out
    assert "anywhere from" in out


def _stroke_file(tmp_path, times):
    p = tmp_path / "deploy.measure.toml"
    p.write_text('[measure]\nkind = "cylinder"\nby = "J. Smith"\ndate = 2026-11-10\n\n[stroke_setup]\n'
                 "bore_in = 1.0625\nrod_in = 0.3125\nstroke_in = 6\nload_mass_lb = 2\nfps = 240\n"
                 + "".join(f"\n[[stroke]]\n{t}\n" for t in times), encoding="utf-8")
    return p


def test_a_good_cylinder_fit_prints_the_strokes_the_average_and_the_band(tmp_path, capsys):
    assert _run(_stroke_file(tmp_path, ["time_s = 0.31", "frames = 74", "time_s = 0.30"])) == 0
    out = capsys.readouterr().out
    assert "stroke: fits the valve's conductance to your strokes" in out
    rows = [ln.split() for ln in out.splitlines() if ln.strip()[:1].isdigit()]
    assert rows[1] == ["2", "74", "0.308", "s"]  # 74 frames at 240 fps
    assert "time: average 0.306 s, lowest 0.300 s, highest 0.310 s, spread 3% (3 trials)" in out
    assert "fit: valve conductance " in out and "fits your strokes (dm^3/(s bar))" in out
    assert "the model's stroke takes 0.3" in out


def test_a_quick_stroke_timed_by_stopwatch_is_not_good_and_says_film_it(tmp_path, capsys):
    s = Stroke(bore=1.0625 * 0.0254, rod=0.3125 * 0.0254, stroke=6 * 0.0254, mass=0.1 + 2 * 0.45359237,
               vertical=False, working_psi=60.0, friction_n=8.0)
    t = stroke_time(s, 2 * DEFAULT_CONDUCTANCE)
    assert _run(_stroke_file(tmp_path, [f"time_s = {t!r}"] * 3)) == 1
    out = capsys.readouterr().out
    assert "too quick to time this closely: film it and count frames" in out
    assert "not good enough yet: the strokes aren't timed closely enough" in out


# ------------------------------------------------------------------------------------------- the form and new file

def test_form_prints_the_form(capsys):
    assert _run("form", "squeeze") == 0
    assert capsys.readouterr().out == forms.form_html("squeeze")


def test_form_writes_the_form(tmp_path, capsys):
    out = tmp_path / "squeeze-form.html"
    assert _run("form", "drop", "-o", out) == 0
    assert out.read_text(encoding="utf-8") == forms.form_html("drop")
    assert str(out) in capsys.readouterr().out


def test_form_for_an_unknown_test_exits_2(capsys):
    assert _run("form", "bounce") == 2
    assert "no test 'bounce'; one of size, squeeze, drop, sled, shot, stroke" in capsys.readouterr().err


def test_form_into_a_missing_folder_exits_2(tmp_path, capsys):
    assert _run("form", "drop", "-o", tmp_path / "no" / "form.html") == 2
    assert capsys.readouterr().err.startswith("scrimmage: ")


def test_new_writes_a_starter_file_that_reads(tmp_path, capsys):
    p = tmp_path / "tread.measure.toml"
    assert _run("new", "sled", p) == 0
    assert "wrote" in capsys.readouterr().out
    text = p.read_text(encoding="utf-8")
    assert 'kind = "friction"' in text and "# [[sled]]" in text
    assert _run(p) == 1  # no trials yet
    assert "no trials yet: write each trial as a [[sled]] table" in capsys.readouterr().out


def test_new_wont_overwrite_a_file(tmp_path, capsys):
    p = tmp_path / "tread.measure.toml"
    p.write_text("mine", encoding="utf-8")
    assert _run("new", "sled", p) == 2
    assert "already exists" in capsys.readouterr().err
    assert p.read_text(encoding="utf-8") == "mine"


def test_new_wants_a_measure_toml_name(tmp_path, capsys):
    assert _run("new", "sled", tmp_path / "tread.toml") == 2
    assert "a measurement file's name ends in .measure.toml" in capsys.readouterr().err
    assert not (tmp_path / "tread.toml").exists()


def test_new_for_an_unknown_test_exits_2(tmp_path, capsys):
    assert _run("new", "bounce", tmp_path / "x.measure.toml") == 2
    assert "no test 'bounce'" in capsys.readouterr().err


def test_measure_with_no_file_exits_2(capsys):
    assert _run() == 2
    assert "scrimmage measure FILE | form TEST [-o PATH] | new TEST PATH" in capsys.readouterr().err


def test_measure_is_listed_in_the_help(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert ("measure measurements: read a <name>.measure.toml file (its averages and fit), print a blank form, or "
            "start a file (docs/measuring.md)") in out


def test_measure_help_lists_its_commands(capsys):
    assert _run("--help") == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "usage: scrimmage measure FILE | form TEST [-o PATH] | new TEST PATH" in out
    assert "scrimmage measure form TEST [-o PATH] writes a printable blank form" in out
    assert "The tests: size, squeeze, drop, sled, shot, stroke." in out
