"""Measurement files, units, averaging and the direct tests (ball size and mass, squeeze, drop, sled pull)."""
import pytest

from scrimmage.launcher.inputs import PRESETS
from scrimmage.measure import file, stats, tests_direct, units
from scrimmage.measure.file import MeasureError, Provenance

LB = 4.4482216152605  # newtons per pound of force
INCH = 0.0254


def _write(tmp_path, text, name="fuel.measure.toml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


BALL_HEADER = """\
[measure]
kind = "ball"
about = "a FUEL ball from the practice bin"
by = "J. Smith"
date = 2026-11-10
tools = "bathroom scale, two boards, tape measure"
base = "2026-fuel"
"""


# ---------------------------------------------------------------------------------------------------- units

def test_units_convert_to_si():
    assert units.reading({"force_lb": 10}, "force") == pytest.approx(44.48, abs=0.005)
    assert units.reading({"mass_oz": 8}, "mass") == pytest.approx(0.2268, abs=5e-5)
    assert units.reading({"length_mm": 150}, "length") == pytest.approx(0.15)
    assert units.reading({"force_kg": 1}, "force") == pytest.approx(9.80665)
    assert units.reading({"drop_in": 18, "bounce_in": 6.48}, "length", "bounce") == pytest.approx(6.48 * INCH)
    assert units.reading({"frames": 12}, "frames") == 12


def test_a_reading_with_no_unit_lists_the_units():
    with pytest.raises(MeasureError, match="force_n, force_lb or force_kg"):
        units.reading({"force": 10}, "force")
    with pytest.raises(MeasureError, match="force_n, force_lb or force_kg"):
        units.reading({}, "force")


def test_an_unknown_unit_or_two_units_error():
    with pytest.raises(MeasureError, match="'stone' isn't a unit of force"):
        units.reading({"force_stone": 1}, "force")
    with pytest.raises(MeasureError, match="force_lb and force_n"):
        units.reading({"force_lb": 1, "force_n": 4.4}, "force")
    with pytest.raises(MeasureError, match="must be a number"):
        units.reading({"force_lb": "10"}, "force")


def test_a_weight_takes_force_and_mass_units():
    assert units.reading({"tare_lb": 1}, "weight", "tare") == pytest.approx(LB)
    assert units.reading({"tare_kg": 1}, "weight", "tare") == pytest.approx(9.80665)
    assert units.reading({"tare_g": 1000}, "weight", "tare") == pytest.approx(9.80665)
    assert units.reading({"tare_oz": 16}, "weight", "tare") == pytest.approx(LB)
    assert units.reading({"tare_n": 3}, "weight", "tare") == 3


def test_an_uppercase_unit_says_units_are_lowercase():
    with pytest.raises(MeasureError, match="units are lowercase: write force_lb"):
        units.reading({"force_LB": 10}, "force")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_a_reading_that_isnt_a_finite_number_is_an_error(value):
    with pytest.raises(MeasureError, match="trial 2: force_lb must be a number"):
        units.reading({"force_lb": value}, "force", where="x.measure.toml: [[squeeze]] trial 2")


def test_an_optional_reading_may_be_left_out():
    assert units.reading({}, "length", "squeezed_to", required=False) is None


# ---------------------------------------------------------------------------------------------------- stats

def test_summary_of_steady_trials_is_good():
    s = stats.summary([9.8, 10.1, 10.0, 9.9, 10.2])
    assert s.mean == pytest.approx(10.0) and s.low == 9.8 and s.high == 10.2 and s.n == 5
    assert s.spread == pytest.approx(0.04)
    assert s.good and s.warnings == [] and s.outliers == []


def test_two_very_different_trials_are_averaged_but_not_good():
    # Review Focus 1: 5 lb and 15 lb.
    s = stats.summary([5.0, 15.0])
    assert s.mean == pytest.approx(10.0)
    assert not s.good
    assert any("take at least 3 trials" in w for w in s.warnings)
    assert any("your trials disagree by 100%" in w for w in s.warnings)


def test_one_trial_twice_the_rest_is_flagged_not_dropped():
    values = [10.0] * 9 + [20.0]
    values[3], values[9] = 20.0, 10.0  # the odd one is trial 4
    s = stats.summary(values)
    assert s.outliers == [3]
    assert "trial 4 looks off: check it" in s.warnings
    assert s.n == 10 and s.mean == pytest.approx(11.0)  # still counted


@pytest.mark.parametrize("values, flagged", [
    ([10.0, 10.0, 100.0], []),
    ([10.0, 10.0, 10.0, 100.0], []),  # exactly twice the average distance: not more than
    ([10.0, 10.0, 10.0, 10.0, 100.0], [4]),
])
def test_no_outlier_can_be_flagged_below_5_trials(values, flagged):
    # One trial's distance from the mean is at most n/2 times the average distance; n/2 > 2 needs n >= 5.
    assert stats.summary(values).outliers == flagged


def test_a_summary_names_trials_by_the_numbers_it_is_given():
    s = stats.summary([1.0, 1.0, 1.0, 1.0, 1.0, 9.0], trials=[0, 2, 3, 4, 5, 6])
    assert s.outliers == [5] and "trial 7 looks off: check it" in s.warnings


def test_a_wide_spread_warns_in_percent():
    s = stats.summary([9.0, 10.0, 11.2])
    assert not s.good
    assert "your trials disagree by 22%: measure again or take more trials" in s.warnings


# ---------------------------------------------------------------------------------------------------- the file

def test_load_reads_the_header_and_trials(tmp_path):
    p = _write(tmp_path, BALL_HEADER + "\n[[size]]\nmass_g = 216\ncircumference_in = 18.85\n")
    mf = file.load(p)
    assert (mf.kind, mf.by, mf.date, mf.base, mf.path) == ("ball", "J. Smith", "2026-11-10", "2026-fuel", p)
    assert mf.tools.startswith("bathroom") and mf.about.startswith("a FUEL")
    assert mf.trials == {"size": [{"mass_g": 216, "circumference_in": 18.85}]}
    assert mf.settings == {}


def test_who_measured_it_and_when_may_be_left_out_or_blank(tmp_path):
    """A student types trials before the header: a file without by and date (or with them blank) still reads."""
    left_out = BALL_HEADER.replace('by = "J. Smith"\n', "").replace("date = 2026-11-10\n", "")
    for header in (left_out, BALL_HEADER.replace('"J. Smith"', '""').replace("2026-11-10", '""')):
        mf = file.load(_write(tmp_path, header + "[[size]]\nmass_g = 216\n"))
        assert (mf.by, mf.date) == ("", "")


def test_a_date_may_be_text(tmp_path):
    p = _write(tmp_path, BALL_HEADER.replace("2026-11-10", '"2026-11-10"') + "[[size]]\nmass_g = 216\n")
    assert file.load(p).date == "2026-11-10"


@pytest.mark.parametrize("change, message", [
    (("kind = \"ball\"", "kind = \"bounce\""), "kind must be one of ball, friction, launcher, cylinder"),
    (("by = \"J. Smith\"", "by = 3"), "by must be text, not 3"),
    (("2026-11-10", "\"Nov 10\""), "date must be a date like 2026-11-10"),
    (("base = \"2026-fuel\"", "base = \"2026-fool\""), "base '2026-fool' isn't a ball preset"),
    (("tools =", "tool ="), "unknown key 'tool' in [measure]"),
])
def test_header_errors_name_the_file_and_key(tmp_path, change, message):
    p = _write(tmp_path, BALL_HEADER.replace(*change) + "[[size]]\nmass_g = 216\n")
    with pytest.raises(MeasureError, match="fuel.measure.toml") as e:
        file.load(p)
    assert message in str(e.value)


def test_a_test_from_another_kind_is_an_error(tmp_path):
    p = _write(tmp_path, BALL_HEADER + "[[sled]]\npull_lb = 2\n")
    with pytest.raises(MeasureError, match=r"\[\[sled\]\] is a friction test; a ball file has size, squeeze, drop"):
        file.load(p)


def test_a_missing_or_broken_file_is_an_error(tmp_path):
    with pytest.raises(MeasureError, match="no such file"):
        file.load(tmp_path / "gone.measure.toml")
    with pytest.raises(MeasureError, match="isn't valid TOML"):
        file.load(_write(tmp_path, "[measure\n"))


def test_the_launcher_and_cylinder_settings_tables_are_kept(tmp_path):
    p = _write(tmp_path, """\
[measure]
kind = "launcher"
by = "J. Smith"
date = 2026-11-10

[shot_setup]
setup = "shooter.toml"

[[shot]]
wheel_rps = 50
marks_in = 12
frames = 9
fps = 240
""")
    mf = file.load(p)
    assert mf.settings == {"shot_setup": {"setup": "shooter.toml"}}
    assert mf.trials["shot"][0]["fps"] == 240
    assert mf.base is None and mf.about == "" and mf.tools == ""


def test_provenance_carries_the_file_and_trial_count(tmp_path):
    p = _write(tmp_path, BALL_HEADER + "[[size]]\nmass_g = 216\n[[size]]\nmass_g = 215\n[[size]]\nmass_g = 217\n")
    mf = file.load(p)
    summary, _ = tests_direct.values(mf)["mass"]
    assert file.provenance(mf, summary) == Provenance(file=p, by="J. Smith", date="2026-11-10", trials=3, good=True,
                                                      why="")


def test_provenance_says_why_it_isnt_good(tmp_path):
    p = _write(tmp_path, BALL_HEADER + "[[size]]\nmass_g = 100\n[[size]]\nmass_g = 122\n")
    mf = file.load(p)
    summary, _ = tests_direct.values(mf)["mass"]
    prov = file.provenance(mf, summary)
    assert not prov.good and prov.why == "2 trials: take at least 3; trials disagree by 20%"


def test_an_empty_trial_list_is_an_error(tmp_path):
    with pytest.raises(MeasureError, match=r"fuel.measure.toml: squeeze has no trials"):
        file.load(_write(tmp_path, "squeeze = []\n" + BALL_HEADER))


# ---------------------------------------------------------------------------------------------------- direct tests

def test_size_gives_mass_and_diameter(tmp_path):
    p = _write(tmp_path, BALL_HEADER + """
[[size]]
mass_g = 216
circumference_in = 18.85

[[size]]
mass_oz = 7.6
diameter_mm = 152.4

[[size]]
mass_g = 215
""")
    v = tests_direct.values(file.load(p))
    summary, per_trial = v["diameter"]
    assert per_trial[0] == pytest.approx(6.0 * INCH, rel=1e-3)
    assert per_trial[1] == pytest.approx(0.1524)
    assert summary.n == 2
    assert v["mass"][1] == pytest.approx([0.216, 7.6 * 0.028349523125, 0.215])


def test_size_needs_one_reading_of_each_kind(tmp_path):
    p = _write(tmp_path, BALL_HEADER + "[[size]]\ncircumference_in = 18.85\ndiameter_in = 6\n")
    with pytest.raises(MeasureError, match=r"\[\[size\]\] trial 1 .*circumference or a diameter, not both"):
        tests_direct.values(file.load(p))
    p = _write(tmp_path, BALL_HEADER + "[[size]]\nwidth_in = 6\n")
    with pytest.raises(MeasureError, match=r"unknown reading 'width_in' in \[\[size\]\] trial 1"):
        tests_direct.values(file.load(p))


def test_squeeze_at_75_percent_minus_the_tare(tmp_path):
    rows = "".join(f"\n[[squeeze]]\nforce_lb = {f}\n" for f in [9.8, 10.1, 10.0, 9.9, 10.2])
    p = _write(tmp_path, BALL_HEADER + "\n[squeeze_setup]\ntare_lb = 1.0\n" + rows)
    summary, per_trial = tests_direct.values(file.load(p))["stiffness_n"]
    assert summary.mean == pytest.approx(8.999 * LB, rel=1e-3)
    assert summary.mean == pytest.approx(40.03, rel=1e-3)
    assert per_trial[0] == pytest.approx(8.8 * LB)
    assert summary.good


def test_squeeze_to_another_height_converts_with_the_exponent(tmp_path):
    d = PRESETS["ball"]["2026-fuel"]["diameter"]
    rows = "".join(f"\n[[squeeze]]\nforce_n = 30\nsqueezed_to_mm = {0.8 * d * 1000}\n" for _ in range(3))
    p = _write(tmp_path, BALL_HEADER + rows)
    summary, _ = tests_direct.values(file.load(p))["stiffness_n"]
    assert summary.mean == pytest.approx(30 * (0.25 / 0.20) ** 1.5)


SIZE3 = "".join("\n[[size]]\ndiameter_in = 6\n" for _ in range(3))
SQUEEZE_TO_4_8 = "".join("\n[[squeeze]]\nforce_n = 30\nsqueezed_to_in = 4.8\n" for _ in range(3))


def test_squeeze_uses_a_good_measured_diameter_over_the_preset(tmp_path):
    summary, _ = tests_direct.values(file.load(_write(tmp_path, BALL_HEADER + SIZE3 + SQUEEZE_TO_4_8)))["stiffness_n"]
    assert summary.mean == pytest.approx(30 * (0.25 / 0.20) ** 1.5)
    assert summary.warnings == []


def test_squeeze_falls_back_to_the_preset_when_the_size_isnt_good(tmp_path):
    d = PRESETS["ball"]["2026-fuel"]["diameter"]
    text = BALL_HEADER + "\n[[size]]\ndiameter_in = 6\n" + SQUEEZE_TO_4_8
    summary, _ = tests_direct.values(file.load(_write(tmp_path, text)))["stiffness_n"]
    assert summary.mean == pytest.approx(30 * (0.25 / (1 - 4.8 * INCH / d)) ** 1.5)
    assert any("2026-fuel preset's diameter" in w and "1 trial: take at least 3" in w for w in summary.warnings)

    text = BALL_HEADER.replace('base = "2026-fuel"\n', "") + "\n[[size]]\ndiameter_in = 6\n" + SQUEEZE_TO_4_8
    summary, _ = tests_direct.values(file.load(_write(tmp_path, text)))["stiffness_n"]
    assert summary.mean == pytest.approx(30 * (0.25 / 0.20) ** 1.5)
    assert any("isn't good enough" in w for w in summary.warnings)


def test_squeeze_to_a_height_needs_a_diameter(tmp_path):
    text = BALL_HEADER.replace('base = "2026-fuel"\n', "") + "[[squeeze]]\nforce_n = 30\nsqueezed_to_in = 4.8\n"
    with pytest.raises(MeasureError, match="needs the ball's diameter"):
        tests_direct.values(file.load(_write(tmp_path, text)))


def test_a_tare_heavier_than_the_reading_is_an_error(tmp_path):
    p = _write(tmp_path, BALL_HEADER + "[squeeze_setup]\ntare_lb = 3\n[[squeeze]]\nforce_lb = 2\n")
    with pytest.raises(MeasureError, match=r"\[\[squeeze\]\] trial 1 reads no more than the tare: zero the scale"):
        tests_direct.values(file.load(p))


def test_a_tare_read_on_a_kitchen_scale_in_grams(tmp_path):
    rows = "".join("\n[[squeeze]]\nforce_n = 40\ntare_g = 500\n" for _ in range(3))
    summary, _ = tests_direct.values(file.load(_write(tmp_path, BALL_HEADER + rows)))["stiffness_n"]
    assert summary.mean == pytest.approx(40 - 0.5 * 9.80665)


@pytest.mark.parametrize("rows, message", [
    ("[[squeeze]]\nforce_n = 30\nsqueezed_to_in = 0\n", "is squeezed to 0: squeezed_to is the gap between the boards"),
    ("[[squeeze]]\nforce_n = 30\nsqueezed_to_in = 7\n", "isn't less than the ball's diameter"),
    ("[drop_setup]\ndrop_in = 0\n[[drop]]\nbounce_in = 0\n", "drop height of 0: measure from the floor"),
    ("[drop_setup]\ndrop_in = 18\n[[drop]]\nbounce_in = 6\nfloor = \"tile\"\n",
     r"floor goes in \[drop_setup\], once for all trials, not in \[\[drop\]\] trial 1"),
])
def test_messages_say_what_to_do(tmp_path, rows, message):
    with pytest.raises(MeasureError, match=message):
        tests_direct.values(file.load(_write(tmp_path, BALL_HEADER + rows)))


def test_drop_gives_restitution(tmp_path):
    rows = "".join("\n[[drop]]\nbounce_in = 6.48\n" for _ in range(3))
    p = _write(tmp_path, BALL_HEADER + "\n[drop_setup]\ndrop_in = 18\nfloor = \"concrete\"\n" + rows)
    summary, per_trial = tests_direct.values(file.load(p))["restitution"]
    assert summary.mean == pytest.approx(0.6) and per_trial == pytest.approx([0.6] * 3)
    assert summary.good and summary.warnings == []


def test_a_drop_on_carpet_or_from_another_height_warns(tmp_path):
    rows = "".join("\n[[drop]]\nbounce_in = 6.48\n" for _ in range(3))
    p = _write(tmp_path, BALL_HEADER + "\n[drop_setup]\ndrop_in = 18\nfloor = \"Carpet\"\n" + rows)
    summary, _ = tests_direct.values(file.load(p))["restitution"]
    assert any("soft floor (Carpet)" in w and "hard floor" in w for w in summary.warnings)
    assert summary.good  # a warning, not a reason to refuse it

    p = _write(tmp_path, BALL_HEADER + "\n[drop_setup]\ndrop_in = 36\nfloor = \"concrete\"\n" + rows)
    summary, _ = tests_direct.values(file.load(p))["restitution"]
    assert any("dropped from 36 in" in w and "18 in" in w for w in summary.warnings)


def test_a_bounce_higher_than_the_drop_is_an_error(tmp_path):
    p = _write(tmp_path, BALL_HEADER + "[drop_setup]\ndrop_in = 18\n[[drop]]\nbounce_in = 19\n")
    with pytest.raises(MeasureError, match=r"\[\[drop\]\] trial 1 bounces higher than the drop"):
        tests_direct.values(file.load(p))


def test_sled_pull_gives_friction(tmp_path):
    rows = "".join("\n[[sled]]\npull_lb = 2.0\n" for _ in range(3))
    p = _write(tmp_path, """\
[measure]
kind = "friction"
by = "J. Smith"
date = 2026-11-10

[sled_setup]
sled_lb = 5.0
sliding = "wheel tread: 40A"
on = "FUEL"
""" + rows)
    summary, per_trial = tests_direct.values(file.load(p))["friction"]
    assert summary.mean == pytest.approx(0.4) and per_trial == pytest.approx([0.4] * 3)


def test_a_sled_names_its_two_materials(tmp_path):
    p = _write(tmp_path, """\
[measure]
kind = "friction"
by = "J. Smith"
date = 2026-11-10

[sled_setup]
sled_lb = 5.0

[[sled]]
pull_lb = 2.0
""")
    with pytest.raises(MeasureError, match=r"\[sled_setup\] is missing required key 'sliding'"):
        tests_direct.values(file.load(p))


def test_a_file_with_only_fit_tests_has_no_direct_values(tmp_path):
    p = _write(tmp_path, '[measure]\nkind = "cylinder"\nby = "J. Smith"\ndate = 2026-11-10\n[[stroke]]\ntime_s = 0.4\n')
    assert tests_direct.values(file.load(p)) == {}


@pytest.mark.parametrize("floor, warned", [
    ("rug", True), ("artificial turf", True), ("gym mat", True), ("foam tiles", True), ("Foam Tile", True),
    ("matte concrete", False), ("tile", False), ("wood", False),
])
def test_soft_floors_warn(tmp_path, floor, warned):
    rows = "".join("\n[[drop]]\nbounce_in = 6.48\n" for _ in range(3))
    p = _write(tmp_path, BALL_HEADER + f"\n[drop_setup]\ndrop_in = 18\nfloor = \"{floor}\"\n" + rows)
    summary, _ = tests_direct.values(file.load(p))["restitution"]
    assert any("soft floor" in w for w in summary.warnings) == warned


def test_a_sled_weight_of_zero_says_to_weigh_it(tmp_path):
    p = _write(tmp_path, FRICTION_HEADER + "sled_lb = 0\n[[sled]]\npull_lb = 2\n")
    with pytest.raises(MeasureError, match="sled weight of 0: weigh the sled"):
        tests_direct.values(file.load(p))


def test_a_sled_weighed_on_a_kitchen_scale(tmp_path):
    rows = "".join("\n[[sled]]\npull_lb = 1.0\n" for _ in range(3))
    p = _write(tmp_path, FRICTION_HEADER + "sled_oz = 40\n" + rows)  # 40 oz is 2.5 lb
    summary, _ = tests_direct.values(file.load(p))["friction"]
    assert summary.mean == pytest.approx(0.4, rel=1e-6)


FRICTION_HEADER = """\
[measure]
kind = "friction"
by = "J. Smith"
date = 2026-11-10

[sled_setup]
sliding = "wheel tread: 40A"
on = "FUEL"
"""
