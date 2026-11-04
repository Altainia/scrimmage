"""The cylinder stroke fit: a valve's conductance, fit to timed strokes from the valve opening to the rod at full
stroke (spec C9)."""
import pytest

from scrimmage.measure import file
from scrimmage.measure.file import MeasureError
from scrimmage.measure.fit_cylinder import ROD_MASS, Stroke, fit, stroke_time
from scrimmage.pneumatics.model import DEFAULT_CONDUCTANCE

INCH = 0.0254
LB = 0.45359237

HEADER = """\
[measure]
kind = "cylinder"
about = "the intake's deploy cylinder"
by = "J. Smith"
date = 2026-11-10
tools = "phone at 240 fps"
"""

# A 1-1/16 in. bore, 6 in. stroke cylinder (the stock part's) pushing a 2 lb intake.
SETUP = """\
[stroke_setup]
bore_in = 1.0625
rod_in = 0.3125
stroke_in = 6
load_mass_lb = 2
"""


def _stroke(load_lb=2.0, vertical=False):
    return Stroke(bore=1.0625 * INCH, rod=0.3125 * INCH, stroke=6 * INCH, mass=ROD_MASS + load_lb * LB,
                  vertical=vertical, working_psi=60.0, friction_n=8.0)


def _file(tmp_path, times, setup=SETUP, extra=""):
    body = HEADER + setup + extra + "".join(f"\n[[stroke]]\ntime_s = {t!r}\n" for t in times)
    p = tmp_path / "deploy.measure.toml"
    p.write_text(body, encoding="utf-8")
    return file.load(p)


def _raw(tmp_path, text):
    p = tmp_path / "deploy.measure.toml"
    p.write_text(HEADER + text, encoding="utf-8")
    return file.load(p)


def test_stroke_time_falls_as_conductance_rises():
    times = [stroke_time(_stroke(), DEFAULT_CONDUCTANCE * r) for r in (0.1, 0.3, 1.0, 2.0, 3.0, 10.0)]
    assert all(a > b for a, b in zip(times, times[1:]))


def _filmed(tmp_path, t, fps=240):
    return _raw(tmp_path, SETUP + f"fps = {fps}\n" + f"\n[[stroke]]\nframes = {t * fps!r}\n" * 3)


def test_recovers_its_own_stroke_time_at_twice_the_default(tmp_path):
    t = stroke_time(_stroke(), 2 * DEFAULT_CONDUCTANCE)
    f = fit(_filmed(tmp_path, t))
    assert f.conductance == pytest.approx(2 * DEFAULT_CONDUCTANCE, rel=0.03)
    assert f.measured_time == pytest.approx(t)
    assert f.model_time == pytest.approx(t, rel=0.001)
    assert abs(f.error) < 0.001
    assert 0.85 * f.conductance < f.band[0] < f.conductance < f.band[1] < 1.15 * f.conductance
    assert f.good, f.warnings
    assert f.why == ""
    assert f.band_text.startswith("valve conductance 1.60, anywhere from ")


def test_a_quick_stroke_timed_in_seconds_is_not_timed_closely_enough(tmp_path):
    t = stroke_time(_stroke(), 2 * DEFAULT_CONDUCTANCE)  # about 0.07 s: 0.01 s is 14% of it
    f = fit(_file(tmp_path, [t, t, t]))
    assert f.conductance == pytest.approx(2 * DEFAULT_CONDUCTANCE, rel=0.03)
    assert not f.good
    assert f.why == "the strokes aren't timed closely enough"
    assert any("too quick to time this closely: film it" in w for w in f.warnings)
    slower = stroke_time(_stroke(), 0.5 * DEFAULT_CONDUCTANCE)  # about 0.24 s
    assert fit(_file(tmp_path, [slower] * 3)).good


def test_a_stroke_faster_than_the_model_can_go_is_at_the_edge(tmp_path):
    fastest = stroke_time(_stroke(), 10 * DEFAULT_CONDUCTANCE)
    f = fit(_file(tmp_path, [0.5 * fastest] * 3))
    assert not f.good
    assert f.conductance == pytest.approx(10 * DEFAULT_CONDUCTANCE)
    assert any("at the edge" in w for w in f.warnings)
    assert any("can't stroke this fast" in w for w in f.warnings)
    assert "edge" in f.why


def test_a_stroke_slower_than_the_slowest_valve_is_at_the_edge(tmp_path):
    slowest = stroke_time(_stroke(), 0.1 * DEFAULT_CONDUCTANCE)
    f = fit(_file(tmp_path, [2 * slowest] * 3))
    assert not f.good
    assert f.conductance == pytest.approx(0.1 * DEFAULT_CONDUCTANCE)
    assert any("at the edge" in w for w in f.warnings)
    assert any("even with the slowest valve it tries" in w for w in f.warnings)


def test_a_vertical_stroke_with_a_load_is_slower(tmp_path):
    assert stroke_time(_stroke(vertical=True), DEFAULT_CONDUCTANCE) > stroke_time(_stroke(), DEFAULT_CONDUCTANCE)
    # the same timed stroke, lifting its load, takes a freer valve
    flat = fit(_file(tmp_path, [0.15] * 3))
    up = fit(_file(tmp_path, [0.15] * 3, setup=SETUP + "vertical = true\n"))
    assert up.conductance > flat.conductance


def test_a_heavy_load_sets_the_time_so_the_fit_is_not_good(tmp_path):
    setup = SETUP.replace("load_mass_lb = 2", "load_mass_kg = 40")
    heavy = Stroke(**{**_stroke().__dict__, "mass": ROD_MASS + 40})
    t = stroke_time(heavy, 4 * DEFAULT_CONDUCTANCE)
    f = fit(_file(tmp_path, [t] * 3, setup=setup))
    assert abs(f.error) < 0.01  # it fits the time, but so does a wide range of valves
    assert not f.good
    assert f.band[1] > 1.15 * f.conductance or f.band[0] < 0.85 * f.conductance
    assert f.why == "the stroke time hardly depends on the valve"
    assert any("hardly depends on the valve: the load sets it: time a lighter stroke" in w for w in f.warnings)


def test_two_strokes_are_reported_but_not_good(tmp_path):
    t = stroke_time(_stroke(), 2 * DEFAULT_CONDUCTANCE)
    f = fit(_raw(tmp_path, SETUP + "fps = 240\n" + f"\n[[stroke]]\nframes = {t * 240!r}\n" * 2))
    assert f.conductance == pytest.approx(2 * DEFAULT_CONDUCTANCE, rel=0.03)
    assert not f.good
    assert f.why == "2 trials: take at least 3"
    assert any("take at least 3 trials" in w for w in f.warnings)


def test_strokes_counted_in_frames(tmp_path):
    t = stroke_time(_stroke(), 2 * DEFAULT_CONDUCTANCE)
    frames = round(t * 240, 1)
    mf = _raw(tmp_path, SETUP + "fps = 240\n" + f"\n[[stroke]]\nframes = {frames}\n" * 2
              + f"\n[[stroke]]\nframes = {frames * 2}\nfps = 480\n")
    f = fit(mf)
    assert f.measured_time == pytest.approx(frames / 240)
    assert f.conductance == pytest.approx(2 * DEFAULT_CONDUCTANCE, rel=0.03)


def test_the_time_resolution_widens_the_band(tmp_path):
    t = stroke_time(_stroke(), 2 * DEFAULT_CONDUCTANCE)
    timed = fit(_file(tmp_path, [t] * 3))  # 0.01 s each
    filmed = fit(_filmed(tmp_path, t, fps=960))  # 1/960 s each, so 2% of the time sets it
    assert filmed.band[1] - filmed.band[0] < timed.band[1] - timed.band[0]


@pytest.mark.parametrize("text, words", [
    (SETUP.replace("bore_in = 1.0625\n", "") + "\n[[stroke]]\ntime_s = 0.1\n",
     ["deploy.measure.toml", "[stroke_setup]", "has no bore", "bore_<unit>"]),
    (SETUP.replace("bore_in", "bore") + "\n[[stroke]]\ntime_s = 0.1\n",
     ["[stroke_setup]", "bore needs its unit"]),
    (SETUP + "\n[[stroke]]\ntime_s = 0.1\n\n[[stroke]]\nfps = 240\n",
     ["[[stroke]] trial 2", "has no time", "valve opening"]),
    (SETUP + "\n[[stroke]]\ntime_s = 0.1\nframes = 24\nfps = 240\n",
     ["[[stroke]] trial 1", "both time and frames"]),
    (SETUP + "\n[[stroke]]\nframes = 24\n", ["[[stroke]] trial 1", "has no fps"]),
    (SETUP + "\n[[stroke]]\ntime_ms = 100\n", ["[[stroke]] trial 1", "'ms' isn't a unit of time"]),
    (SETUP + "colour = 1\n\n[[stroke]]\ntime_s = 0.1\n", ["unknown key 'colour' in [stroke_setup]"]),
    (SETUP + "\n[[stroke]]\ntime_s = 0.1\nbore_in = 1\n", ["bore_in goes in [stroke_setup]", "trial 1"]),
    (SETUP + "vertical = 1\n\n[[stroke]]\ntime_s = 0.1\n", ["vertical must be true or false"]),
    (SETUP + "working_psi = 80\n\n[[stroke]]\ntime_s = 0.1\n", ["[stroke_setup]", "at most 60 psi"]),
    (SETUP.replace("rod_in = 0.3125", "rod_in = 1.5") + "\n[[stroke]]\ntime_s = 0.1\n",
     ["rod must be thinner than the bore"]),
    (SETUP.replace("load_mass_lb = 2", "load_mass_kg = 200") + "vertical = true\n\n[[stroke]]\ntime_s = 0.1\n",
     ["[stroke_setup]", "can't lift its load at 60 psi"]),
])
def test_errors_name_the_file_table_and_trial(tmp_path, text, words):
    with pytest.raises(MeasureError) as e:
        fit(_raw(tmp_path, text))
    for w in words:
        assert w in str(e.value)


def test_only_a_cylinder_file_fits(tmp_path):
    p = tmp_path / "ball.measure.toml"
    p.write_text(HEADER.replace('"cylinder"', '"ball"') + "\n[[squeeze]]\nforce_lb = 9\n", encoding="utf-8")
    with pytest.raises(MeasureError, match="only a cylinder file has strokes"):
        fit(file.load(p))
