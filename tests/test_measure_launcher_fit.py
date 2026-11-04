"""The launcher shot fit: a ball's stiffness and a wheel's friction fit to filmed shots' exit speeds (spec C8)."""
import dataclasses
import time

import pytest

from scrimmage.launcher.inputs import load_setup
from scrimmage.measure import file
from scrimmage.measure.file import MeasureError
from scrimmage.measure.fit_launcher import exit_speed, fit

INCH = 0.0254
MARKS_IN = 12.0
FPS = 240.0

# A one-wheel hooded launcher shooting the 2026 FUEL ball (examples/launcher/hooded.toml, with the hood gap made for
# a 5.9 in. ball: a 1.6 in. squeeze, 27% of it). Its numbers are the presets': stiffness_n 80, wheel friction 0.95.
# The squeeze is chosen so neither test speed is near the wheel's own surface speed limit: at a 0.12 m gap the
# 50 rps shot is, its exit speed hardly moves with stiffness or friction, and the 80 rps shot alone can't tell a
# stiffer ball from a grippier wheel past the model's own 0.03% step-to-step jitter.
SETUP = """\
[ball]
preset = "2026-fuel"

[wheels]
preset = "35A"
diameter = 0.1016
roller = true
width = 0.127

[shaft]
wheel_inertia = 0.0006
added_inertia = 0.002
motor = "KrakenX60"

[control]
target_rps = 66.7
kP = 0.2
stator_limit = 80.0
supply_limit = 60.0

[hood]
gap = 0.11
exit_angle_deg = 50.0
friction = 0.5

[launcher]
height = 0.6
width = 0.2

[goal]
distance = 4.0
height = 2.5
diameter = 0.76

[feed]
lanes = [0.0]
"""

HEADER = """\
[measure]
kind = "launcher"
about = "the practice robot's shooter"
by = "J. Smith"
date = 2026-11-10
tools = "phone at 240 fps, tape marks 12 in apart"

[shot_setup]
setup = "shooter.toml"
"""


def _setup(tmp_path):
    p = tmp_path / "shooter.toml"
    p.write_text(SETUP, encoding="utf-8")
    return load_setup(p)[0]


def _truth(setup, stiffness_n, friction):
    return dataclasses.replace(setup, ball=dataclasses.replace(setup.ball, stiffness_n=stiffness_n),
                               wheels=dataclasses.replace(setup.wheels, friction=friction))


def _frames(speed):
    """The frames a ball at `speed` takes between the marks (a fraction: the synthetic shots are exact)."""
    return MARKS_IN * INCH * FPS / speed


def _measure(tmp_path, shots, header=HEADER):
    _setup(tmp_path)
    rows = "".join(f"\n[[shot]]\nwheel_rps = {rps}\nmarks_in = {MARKS_IN}\nframes = {frames!r}\nfps = {FPS}\n"
                   for rps, frames in shots)
    p = tmp_path / "shooter.measure.toml"
    p.write_text(header + rows, encoding="utf-8")
    return file.load(p)


def _synthetic(tmp_path, stiffness_n, friction, speeds):
    truth = _truth(_setup(tmp_path), stiffness_n, friction)
    return _measure(tmp_path, [(rps, _frames(exit_speed(truth, rps))) for rps in speeds])


def test_the_fit_recovers_the_stiffness_and_friction_the_shots_were_made_with(tmp_path):
    mf = _synthetic(tmp_path, 120.0, 1.1, [50.0, 50.0, 50.0, 80.0, 80.0, 80.0])
    result = fit(mf)
    assert result.fitted == ("stiffness_n", "friction")
    assert result.stiffness_n == pytest.approx(120.0, rel=0.05)
    assert result.friction == pytest.approx(1.1, rel=0.05)
    assert result.rms_error < 0.01
    assert result.good and result.why == "" and result.warnings == []
    assert len(result.per_shot) == 6
    for measured, model in result.per_shot:
        assert model == pytest.approx(measured, rel=0.02)


def test_a_shot_s_exit_speed_is_the_mark_spacing_times_fps_over_the_frames(tmp_path):
    mf = _measure(tmp_path, [(60.0, 10.0)])
    assert fit(mf).per_shot[0][0] == pytest.approx(MARKS_IN * INCH * FPS / 10.0)


def test_shots_at_one_wheel_speed_fit_only_the_stiffness_and_say_why(tmp_path):
    # Review Focus 2: friction stays at the setup's own 0.95.
    mf = _synthetic(tmp_path, 120.0, 0.95, [80.0, 80.0, 80.0])
    result = fit(mf)
    assert result.fitted == ("stiffness_n",)
    assert result.stiffness_n == pytest.approx(120.0, rel=0.05)
    assert result.friction == 0.95
    assert any("shoot at a second wheel speed to fit friction too" in w for w in result.warnings)
    assert result.good  # the stiffness fits; friction just isn't fit


def test_shots_faster_than_any_setting_gives_are_at_the_edge_and_not_good(tmp_path):
    # Review Focus 3: 14 and 20 m/s from a 4 in. wheel at 50 and 80 rps (its surface moves at 16 and 26 m/s).
    mf = _measure(tmp_path, [(50.0, _frames(14.0)), (80.0, _frames(20.0))])
    result = fit(mf)
    assert not result.good
    assert any("at the edge of what it tries: check your shots" in w for w in result.warnings)
    assert "edge" in result.why


def test_six_shots_fit_within_20_seconds(tmp_path):
    mf = _synthetic(tmp_path, 120.0, 1.1, [40.0, 50.0, 60.0, 70.0, 80.0, 90.0])
    start = time.perf_counter()
    result = fit(mf)
    assert time.perf_counter() - start < 20.0
    assert result.stiffness_n == pytest.approx(120.0, rel=0.05)
    assert result.friction == pytest.approx(1.1, rel=0.05)


def test_a_shot_may_set_its_hood_angle_and_the_setup_table_its_camera(tmp_path):
    truth = _truth(_setup(tmp_path), 120.0, 1.1)
    shots = [(rps, hood, _frames(exit_speed(truth, rps, hood))) for rps, hood in ((50.0, 40.0), (80.0, 60.0))]
    rows = "".join(f"\n[[shot]]\nwheel_rps = {rps}\nhood_deg = {hood}\nframes = {frames!r}\n"
                   for rps, hood, frames in shots)
    p = tmp_path / "shooter.measure.toml"
    p.write_text(HEADER + f"marks_in = {MARKS_IN}\nfps = {FPS}\n" + rows, encoding="utf-8")
    result = fit(file.load(p))
    for measured, model in result.per_shot:
        assert model == pytest.approx(measured, rel=0.02)


def test_a_missing_setup_file_names_where_it_looked(tmp_path):
    mf = _measure(tmp_path, [(60.0, 10.0)], header=HEADER.replace("shooter.toml", "gone.toml"))
    with pytest.raises(MeasureError, match=r"\[shot_setup\] setup = \"gone.toml\": no such file \(looked in .*gone"):
        fit(mf)


def test_a_launcher_file_needs_its_setup(tmp_path):
    p = tmp_path / "shooter.measure.toml"
    p.write_text(HEADER.replace('setup = "shooter.toml"\n', "") + "\n[[shot]]\nwheel_rps = 60\nmarks_in = 12\n"
                 "frames = 10\nfps = 240\n", encoding="utf-8")
    with pytest.raises(MeasureError, match=r"\[shot_setup\] is missing required key 'setup'"):
        fit(file.load(p))


def test_shot_readings_are_checked(tmp_path):
    _setup(tmp_path)
    cases = [
        ("wheel_rps = 60\nmarks_in = 12\nframes = 10\nfps = 240\nspeed = 3\n", "unknown reading 'speed'"),
        ("marks_in = 12\nframes = 10\nfps = 240\n", "trial 1 has no wheel_rps"),
        ("wheel_rps = 60\nmarks = 12\nframes = 10\nfps = 240\n", "marks needs its unit"),
        ("wheel_rps = 60\nmarks_in = 12\nfps = 240\n", "no frames reading"),
        ("wheel_rps = 60\nmarks_in = 12\nframes = 10\n", "has no fps"),
        ("wheel_rps = 60\nmarks_in = 12\nframes = 0\nfps = 240\n", "frames must be above 0"),
        ("wheel_rps = 60\nhood_deg = 170\nmarks_in = 12\nframes = 10\nfps = 240\n", "hood_deg 170"),
    ]
    for row, message in cases:
        p = tmp_path / "shooter.measure.toml"
        p.write_text(HEADER + "\n[[shot]]\n" + row, encoding="utf-8")
        with pytest.raises(MeasureError, match=message):
            fit(file.load(p))


def test_only_a_launcher_file_has_shots_to_fit(tmp_path):
    p = tmp_path / "fuel.measure.toml"
    p.write_text('[measure]\nkind = "ball"\nby = "J. Smith"\ndate = 2026-11-10\n\n[[size]]\nmass_g = 216\n',
                 encoding="utf-8")
    with pytest.raises(MeasureError, match="is a ball measurement: only a launcher file has shots to fit"):
        fit(file.load(p))
