"""The launcher shot fit: a wheel's friction (the ball's squeeze measured) or else a ball's stiffness, fit to filmed
shots' horizontal speeds (spec C8)."""
import time

import pytest

from scrimmage.measure import file
from scrimmage.measure.file import MeasureError
from scrimmage.measure.fit_launcher import SQUEEZE_SENTENCE, fit, horizontal_speed

from measure_helpers import FPS, HEADER, INCH, MARKS_IN, SETUP, WITH_BALL
from measure_helpers import ball_file as _ball_file
from measure_helpers import frames as _frames
from measure_helpers import measure_file as _measure
from measure_helpers import synthetic as _synthetic
from measure_helpers import truth as _truth
from measure_helpers import write_setup as _setup


def _setup_names_the_ball(tmp_path, name="fuel.measure.toml"):
    """The shots' setup names the ball file in its own [ball] measured too, as a run would."""
    (tmp_path / "shooter.toml").write_text(SETUP.replace('preset = "2026-fuel"\n',
                                                         f'preset = "2026-fuel"\nmeasured = "{name}"\n'),
                                           encoding="utf-8")


def test_with_the_squeeze_measured_the_shots_fit_the_wheel_friction(tmp_path):
    _ball_file(tmp_path, [118.0, 120.0, 122.0])
    mf = _synthetic(tmp_path, 120.0, 1.1, [50.0, 50.0, 50.0, 80.0, 80.0, 80.0], header=WITH_BALL)
    _setup_names_the_ball(tmp_path)
    result = fit(mf)
    assert result.fitted == ("friction",)
    assert result.stiffness_n == pytest.approx(120.0)
    assert result.friction == pytest.approx(1.1, rel=0.03)
    assert result.rms_error < 0.01
    assert result.good and result.why == "" and result.warnings == []
    assert len(result.per_shot) == 6
    for measured, model in result.per_shot:
        assert model == pytest.approx(measured, rel=0.02)


def test_without_the_squeeze_the_shots_fit_the_stiffness_and_say_how_to_fit_friction(tmp_path):
    mf = _synthetic(tmp_path, 120.0, 0.95, [50.0, 80.0])  # the setup's own friction, 0.95
    result = fit(mf)
    assert result.fitted == ("stiffness_n",)
    assert result.stiffness_n == pytest.approx(120.0, rel=0.05)
    assert result.friction == 0.95
    assert result.warnings == [SQUEEZE_SENTENCE]
    assert result.good


def test_a_setup_whose_ball_names_a_squeeze_fits_friction(tmp_path):
    _ball_file(tmp_path, [120.0, 120.0, 120.0])
    mf = _synthetic(tmp_path, 120.0, 1.1, [50.0, 80.0])
    (tmp_path / "shooter.toml").write_text(SETUP.replace('preset = "2026-fuel"\n',
                                                         'preset = "2026-fuel"\nmeasured = "fuel.measure.toml"\n'),
                                           encoding="utf-8")
    result = fit(mf)
    assert result.fitted == ("friction",)
    assert result.friction == pytest.approx(1.1, rel=0.03)


def test_a_setup_that_names_this_launcher_file_is_read_without_it(tmp_path):
    # The setup may name the shots' own file (as Task 4 allows); the fit reads the setup as written, no loop.
    mf = _synthetic(tmp_path, 120.0, 0.95, [50.0, 80.0])
    (tmp_path / "shooter.toml").write_text(SETUP.replace('preset = "2026-fuel"\n',
                                                         'preset = "2026-fuel"\nmeasured = "shooter.measure.toml"\n'),
                                           encoding="utf-8")
    result = fit(mf)
    assert result.fitted == ("stiffness_n",)
    assert result.stiffness_n == pytest.approx(120.0, rel=0.05)


def test_a_squeeze_that_isn_t_good_is_held_as_a_run_holds_it_and_the_fit_isn_t_good(tmp_path):
    # A run uses a squeeze that isn't good (its label stays UNCALIBRATED), so the fit holds it too.
    _ball_file(tmp_path, [118.0, 122.0])
    mf = _synthetic(tmp_path, 120.0, 1.1, [50.0, 80.0], header=WITH_BALL)
    _setup_names_the_ball(tmp_path)
    result = fit(mf)
    assert result.fitted == ("friction",)
    assert result.stiffness_n == pytest.approx(120.0)
    assert result.friction == pytest.approx(1.1, rel=0.03)
    assert not result.good
    assert result.why == "the ball's squeeze isn't good enough: 2 trials; take at least 3"
    assert ("the ball's squeeze (fuel.measure.toml) isn't good enough: 2 trials; take at least 3. The fit holds it, "
            "as a run does, so the fit isn't good until it is") in result.warnings


def test_a_sled_pull_that_isn_t_good_is_held_and_the_fit_isn_t_good(tmp_path):
    (tmp_path / "tread.measure.toml").write_text(
        '[measure]\nkind = "friction"\n\n[sled_setup]\nsliding = "wheel tread: 35A"\non = "FUEL"\nsled_lb = 5.0\n'
        + "".join("\n[[sled]]\npull_lb = 5.5\n" for _ in range(2)), encoding="utf-8")
    mf = _synthetic(tmp_path, 120.0, 1.1, [50.0, 80.0])
    (tmp_path / "shooter.toml").write_text(SETUP.replace('preset = "35A"\n',
                                                         'preset = "35A"\nmeasured = "tread.measure.toml"\n'),
                                           encoding="utf-8")
    result = fit(mf)
    assert result.fitted == ("stiffness_n",)
    assert result.friction == pytest.approx(1.1)
    assert not result.good
    assert result.why == "the wheel's sled pull isn't good enough: 2 trials; take at least 3"


def test_a_ball_named_only_by_the_shots_says_to_name_it_in_the_setup(tmp_path):
    _ball_file(tmp_path, [120.0, 120.0, 120.0])
    _ball_file(tmp_path, [100.0, 100.0, 100.0], name="other.measure.toml")
    mf = _synthetic(tmp_path, 120.0, 1.1, [50.0, 80.0], header=WITH_BALL)
    warning = ('[shot_setup] ball = "fuel.measure.toml" isn\'t the ball the setup names: name it in the setup\'s '
               '[ball] measured too, so a run uses the ball the shots were fit with')
    assert warning in fit(mf).warnings  # the setup names no ball file
    _setup_names_the_ball(tmp_path, "other.measure.toml")
    assert warning in fit(mf).warnings  # the setup names another
    _setup_names_the_ball(tmp_path)
    assert warning not in fit(mf).warnings


def test_a_ball_file_must_be_a_ball_measurement(tmp_path):
    _setup(tmp_path)
    mf = _measure(tmp_path, [(60.0, 10.0)], header=WITH_BALL)
    (tmp_path / "fuel.measure.toml").write_text('[measure]\nkind = "friction"\nby = "J. Smith"\n'
                                                'date = 2026-11-10\n', encoding="utf-8")
    with pytest.raises(MeasureError, match=r"\[shot_setup\]: ball = \"fuel.measure.toml\" is a friction measurement; "
                                           "a ball needs a ball measurement"):
        fit(mf)


def test_a_ball_file_that_can_t_be_read_is_named_with_the_shot_setup_that_names_it(tmp_path):
    _setup(tmp_path)
    mf = _measure(tmp_path, [(60.0, 10.0)], header=WITH_BALL)
    (tmp_path / "fuel.measure.toml").write_text("[measure]\nkind = \"ball\"\nwho = \"J. Smith\"\n", encoding="utf-8")
    with pytest.raises(MeasureError, match=r"\[shot_setup\]: ball = \"fuel.measure.toml\": .*fuel.measure.toml: "
                                           r"unknown key 'who' in \[measure\]"):
        fit(mf)


def test_a_shot_s_exit_speed_is_the_mark_spacing_times_fps_over_the_frames(tmp_path):
    mf = _measure(tmp_path, [(60.0, 10.0)])
    assert fit(mf).per_shot[0][0] == pytest.approx(MARKS_IN * INCH * FPS / 10.0)


def test_shots_at_one_wheel_speed_still_fit_with_a_warning(tmp_path):
    _ball_file(tmp_path, [120.0, 120.0, 120.0])
    mf = _synthetic(tmp_path, 120.0, 1.1, [80.0, 80.0, 80.0], header=WITH_BALL)
    _setup_names_the_ball(tmp_path)
    result = fit(mf)
    assert result.friction == pytest.approx(1.1, rel=0.03)
    assert result.warnings == ["the shots are all at one wheel speed (80 rps): shots at two or more wheel speeds "
                               "check the fit"]
    assert result.good


FAST = [(50.0, _frames(14.0)), (80.0, _frames(20.0))]  # a 4 in. wheel's surface moves at 16 and 26 m/s there


def test_shots_faster_than_any_friction_gives_are_at_the_edge_and_not_good(tmp_path):
    # Review Focus 3: the model gives at most about 7 and 12 m/s at these speeds.
    _ball_file(tmp_path, [120.0, 120.0, 120.0])
    result = fit(_measure(tmp_path, FAST, header=WITH_BALL))
    assert result.friction == pytest.approx(2.0)
    assert not result.good
    assert any("the wheel friction, 2.00, is at the edge of what it tries: check your shots" in w
               for w in result.warnings)
    assert "edge" in result.why


def test_shots_slower_than_any_stiffness_gives_are_at_the_edge_and_not_good(tmp_path, units):
    units("metric")   # the wording in newtons; tests/test_display_units_bench.py has both systems
    result = fit(_measure(tmp_path, [(50.0, _frames(1.5)), (80.0, _frames(2.0))]))
    assert result.stiffness_n == pytest.approx(20.0)  # a quarter of the preset's 80 N
    assert not result.good
    assert any("the ball's stiffness, 20 N, is at the edge of what it tries: check your shots" in w
               for w in result.warnings)


def test_shots_faster_than_any_stiffness_gives_are_not_good(tmp_path):
    # The model's exit speed here is highest at about 200 N and falls beyond it, so the fit stops there, short of
    # its limit, and the miss itself makes it not good.
    result = fit(_measure(tmp_path, FAST))
    assert not result.good
    assert result.rms_error > 0.3
    assert any("miss your shots by" in w for w in result.warnings)
    assert ("the model's ball can't go this fast at these wheel speeds: check the wheel speed you wrote and the mark "
            "spacing") in result.warnings


def test_shots_that_hardly_depend_on_the_stiffness_are_not_good(tmp_path):
    # At 50 rps the wheel's speed, not the ball, sets the exit speed: a wide range of stiffness fits the shots.
    result = fit(_synthetic(tmp_path, 120.0, 0.95, [50.0, 50.0, 50.0]))
    assert result.fitted == ("stiffness_n",)
    assert not result.good
    assert result.band[0] < 0.85 * result.stiffness_n or result.band[1] > 1.15 * result.stiffness_n
    assert any("your shots hardly depend on the ball's stiffness at these wheel speeds" in w for w in result.warnings)
    assert "hardly depend" in result.why


def test_shots_that_hardly_depend_on_the_friction_are_not_good(tmp_path):
    _ball_file(tmp_path, [120.0, 120.0, 120.0])
    result = fit(_synthetic(tmp_path, 120.0, 1.1, [50.0, 50.0, 50.0], header=WITH_BALL))
    assert result.fitted == ("friction",)
    assert not result.good
    assert any("your shots hardly depend on the wheel friction at these wheel speeds" in w for w in result.warnings)


def test_a_good_fit_has_a_narrow_band(tmp_path, units):
    units("metric")   # the wording in newtons; tests/test_display_units_bench.py has both systems
    result = fit(_synthetic(tmp_path, 120.0, 0.95, [50.0, 80.0]))
    low, high = result.band
    assert 0.85 * result.stiffness_n <= low < result.stiffness_n < high <= 1.15 * result.stiffness_n
    assert result.band_text == (f"stiffness {result.stiffness_n:.0f} N, anywhere from {low:.0f} to {high:.0f} N fits "
                                "your shots")
    assert result.good


def test_a_setup_whose_wheels_name_a_sled_test_fits_stiffness_at_that_friction(tmp_path):
    (tmp_path / "tread.measure.toml").write_text(
        '[measure]\nkind = "friction"\nby = "J. Smith"\ndate = 2026-11-10\n\n'
        '[sled_setup]\nsliding = "wheel tread: 35A"\non = "FUEL"\nsled_lb = 5.0\n'
        + "".join("\n[[sled]]\npull_lb = 5.5\n" for _ in range(3)), encoding="utf-8")
    mf = _synthetic(tmp_path, 120.0, 1.1, [50.0, 80.0])
    (tmp_path / "shooter.toml").write_text(SETUP.replace('preset = "35A"\n',
                                                         'preset = "35A"\nmeasured = "tread.measure.toml"\n'),
                                           encoding="utf-8")
    result = fit(mf)
    assert result.fitted == ("stiffness_n",)
    assert result.friction == pytest.approx(1.1)
    assert result.stiffness_n == pytest.approx(120.0, rel=0.05)
    assert result.good


def test_six_shots_fit_within_10_seconds(tmp_path):
    _ball_file(tmp_path, [120.0, 120.0, 120.0])
    mf = _synthetic(tmp_path, 120.0, 1.1, [40.0, 50.0, 60.0, 70.0, 80.0, 90.0], header=WITH_BALL)
    start = time.perf_counter()
    result = fit(mf)
    assert time.perf_counter() - start < 10.0
    assert result.friction == pytest.approx(1.1, rel=0.03)


def test_a_shot_may_set_its_hood_angle_and_the_setup_table_its_camera(tmp_path):
    truth = _truth(_setup(tmp_path), 120.0, 1.1)
    shots = [(rps, hood, _frames(horizontal_speed(truth, rps, hood))) for rps, hood in ((50.0, 40.0), (80.0, 60.0))]
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
        ('wheel_rps = 60\nmarks_in = 12\nframes = 10\nfps = 240\nball = "fuel.measure.toml"\n',
         "ball goes in \\[shot_setup\\]"),
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


def test_a_shot_spanning_too_few_frames_is_warned_by_its_number(tmp_path):
    result = fit(_measure(tmp_path, [(60.0, 70.0), (80.0, 12.0), (80.0, 19.5)]))
    assert [w for w in result.warnings if w.startswith("shot ")] == [
        "shot 2: 12 frames between the marks; 20 or more fits better",
        "shot 3: 19.5 frames between the marks; 20 or more fits better"]
