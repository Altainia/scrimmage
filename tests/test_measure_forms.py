"""The printable forms, the starter files and the illustrations (spec goals 1 and 3, C1, C3)."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scrimmage.measure import file, fit_cylinder, fit_launcher, forms, tests_direct
from scrimmage.measure.file import KIND_TESTS
from scrimmage.measure.units import UNITS

REPO = Path(__file__).resolve().parent.parent
IMG = REPO / "scrimmage" / "measure" / "img"
DOCS_IMG = REPO / "docs" / "img" / "measure"
PT = 25.4 / 72  # mm
INCH = 0.0254


def test_the_forms_cover_every_test():
    assert set(forms.TESTS) == set(forms.TEST) == {t for tests in KIND_TESTS.values() for t in tests}


# ------------------------------------------------------------------------------------------------------- the forms

@pytest.mark.parametrize("test", forms.TESTS)
def test_a_form_has_8_rows_the_picture_print_rules_and_no_script(test):
    page = forms.form_html(test)
    assert page.startswith("<!doctype html>")
    assert len(re.findall(r'<td class="n">\d</td>', page)) == forms.ROWS == 8
    assert forms.picture(test) in page
    assert "<svg" in page and "viewBox" in page
    assert "@page" in page
    assert "<script" not in page.lower() and "javascript:" not in page.lower()
    assert not re.search(r"\son\w+=", page)  # no event handlers
    assert not re.search(r"(src|href)=\"https?:", page)  # self-contained
    for words in ("Measured by:", "Date:", "Tools:", "What you measured:"):
        assert words in page
    assert "Type these into Scrimmage's Measure tab, or a <i>name</i>.measure.toml file" in page
    assert f"scrimmage measure new {test}" in page


@pytest.mark.parametrize("test", forms.TESTS)
def test_a_form_prints_every_reading_with_its_units(test):
    page = forms.form_html(test)
    t = forms.TEST[test]
    for r in (*t.setup, *t.trial):
        assert f"<b>{r.name}</b>" in page
        names = forms.units(r)
        if names and names != [r.name]:  # frames are just frames
            assert f'<b>{r.name}</b> <span class="small">{"  ".join(names)}' in page


@pytest.mark.parametrize("test", [t for t in forms.TESTS if t != "shot"])
def test_an_averaged_form_has_the_summary_boxes_and_the_value_formula(test):
    page = forms.form_html(test)
    t = forms.TEST[test]
    assert t.value and t.formula
    assert f'<th>{t.value}<br><span class="small">{t.formula}</span></th>' in page
    for name in t.summaries:
        assert f'<div class="name">{name}</div>' in page
    for box in ("average", "lowest", "highest", "spread %"):
        assert page.count(f'<div class="box">{box}</div>') == len(t.summaries)
    assert page.count("spread under 15%? If not, measure again.") == len(t.summaries)
    assert "Take at least 3 trials; 5 is better." in page
    assert "A trial that looks off can only be flagged with 5 or more trials." in page


def test_the_shot_form_works_out_each_speed_and_says_why_it_has_no_average():
    page = forms.form_html("shot")
    assert ('<th>speed across<br><span class="small">marks × fps ÷ frames: the marks\' unit (in, mm, cm, m) per '
            'second</span></th>') in page
    assert '<div class="box">average</div>' not in page
    assert "Scrimmage fits the shots to the launcher model: they aren't averaged." in page
    assert "two or more, 10% or more apart" in page


def test_the_size_form_averages_mass_and_diameter():
    assert forms.TEST["size"].summaries == ("mass", "diameter")
    assert "circumference ÷ 3.1416" in forms.form_html("size")


def test_the_outlier_count_follows_the_averaging_rule():
    # one trial's distance is at most n/2 average distances, which exceeds 2 only from 5 trials
    assert forms.OUTLIER_TRIALS == 5


# what each form's steps must teach (the build's findings, Tasks 1-3)
TEACHES = {
    "size": ("widest part", "circumference", "calipers", "kitchen scale"),
    "squeeze": ("bottom board", "zero the scale", "tare", "75% of the ball's diameter", "read the scale"),
    "drop": ("18 in", "not carpet", "bottom of the ball", "filming", "against the ruler",
             "level with where the top of the bounce will be"),
    "sled": ("steadily", "not the first jerk", "Weigh the sled"),
    "shot": ("ONE ball", "not the setpoint", "settle", "highest frame rate", "vertical marks",
             "a few inches behind the ball's path", "20 or more frames", "24 in at 960 fps", "square-on",
             "10 or more times as far", "Count the frames", "speed across", "two or more wheel speeds"),
    "stroke": ("light", "Pneumatic Hub", "by the click", "full stroke", "under 0.2 s",
               "count the frames", "stopwatch can't time it"),
}


@pytest.mark.parametrize("test", forms.TESTS)
def test_a_form_teaches_its_test(test):
    steps = " ".join(forms.TEST[test].steps)
    for words in TEACHES[test]:
        assert words in steps, words


def _names(t):
    return {r.name for r in (*t.setup, *t.trial)}


def test_the_forms_ask_for_every_reading_the_files_take():
    for test in tests_direct.TRIAL:
        expected = set(tests_direct.TRIAL[test]) | set(tests_direct.SETTING[test]) | set(tests_direct.SETUP_TEXT[test])
        assert _names(forms.TEST[test]) == expected, test
    shot = (set(fit_launcher.SHOT_READINGS) | set(fit_launcher.SHOT_NUMBERS) | set(fit_launcher.SETUP_TEXT)
            | set(fit_launcher.SETUP_NUMBERS))
    assert _names(forms.TEST["shot"]) == shot
    stroke = (set(fit_cylinder.SETUP_READINGS) | set(fit_cylinder.SETUP_ONLY) | set(fit_cylinder.STROKE_READINGS)
              | set(fit_cylinder.STROKE_NUMBERS))
    assert _names(forms.TEST["stroke"]) == stroke


def test_every_reading_with_a_quantity_has_units_the_files_take():
    for t in forms.TEST.values():
        for r in (*t.setup, *t.trial):
            if r.quantity is not None:
                assert r.quantity in UNITS
                if r.example is not None:
                    assert (r.unit or next(iter(UNITS[r.quantity]))) in UNITS[r.quantity]


def test_an_unknown_test_has_no_form():
    with pytest.raises(ValueError, match="one of size, squeeze, drop, sled, shot, stroke"):
        forms.form_html("bounce")


# ------------------------------------------------------------------------------------------------ the starter files

EXAMPLE = re.compile(r"^# (\[\[\w+\]\]|\w+ = .*)$")


def _uncommented(text: str, drop=()) -> str:
    out = []
    for line in text.splitlines():
        if EXAMPLE.match(line):
            line = line[2:]
            if any(line.startswith(d) for d in drop):
                continue
        out.append(line)
    return "\n".join(out) + "\n"


@pytest.mark.parametrize("test", forms.TESTS)
def test_a_starter_file_reads_as_it_is_with_no_trials(tmp_path, test):
    p = tmp_path / f"{test}.measure.toml"
    p.write_text(forms.starter_toml(test), encoding="utf-8")
    mf = file.load(p)
    assert mf.kind == file.TEST_KIND[test] and mf.trials == {}


@pytest.mark.parametrize("test", ["size", "squeeze", "drop", "sled"])
def test_a_starter_files_examples_read_when_uncommented(tmp_path, test):
    p = tmp_path / f"{test}.measure.toml"
    p.write_text(_uncommented(forms.starter_toml(test)), encoding="utf-8")
    mf = file.load(p)
    assert len(mf.trials[test]) == 1
    fields = {"size": {"mass", "diameter"}, "squeeze": {"stiffness_n"}, "drop": {"restitution"},
              "sled": {"friction"}}[test]
    assert set(tests_direct.values(mf)) == fields


def test_the_stroke_starters_examples_fit_when_uncommented(tmp_path):
    p = tmp_path / "deploy.measure.toml"
    p.write_text(_uncommented(forms.starter_toml("stroke")), encoding="utf-8")
    assert fit_cylinder.fit(file.load(p)).model_time > 0


def test_the_shot_starters_examples_fit_when_uncommented(tmp_path):
    from measure_helpers import SETUP
    (tmp_path / "shooter.toml").write_text(SETUP, encoding="utf-8")
    p = tmp_path / "shots.measure.toml"
    p.write_text(_uncommented(forms.starter_toml("shot"), drop=("ball =",)), encoding="utf-8")
    result = fit_launcher.fit(file.load(p))
    assert result.fitted == ("stiffness_n",) and len(result.per_shot) == 1


def test_a_starter_file_is_dated_and_names_its_kind():
    import datetime
    text = forms.starter_toml("sled", datetime.date(2026, 11, 10))
    assert 'kind = "friction"' in text and "date = 2026-11-10" in text
    assert "# pull_lb = 4.1" in text and "pull_n" in text  # the units it may be written in


# --------------------------------------------------------------------------------------------------- the pictures

GRAY = re.compile(r"#([0-9a-fA-F])\1\1$|#([0-9a-fA-F]{2})\2\2$")


def _gray(color: str) -> bool:
    c = color.strip().lower()
    return c in ("none", "black", "white", "gray", "grey", "transparent") or bool(GRAY.match(c))


@pytest.mark.parametrize("test", forms.TESTS)
def test_a_picture_is_black_and_white_line_art_with_a_viewbox(test):
    path = IMG / f"{test}.svg"
    assert path.stat().st_size < 15_000
    root = ET.parse(path).getroot()
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    x, y, w, h = map(float, root.get("viewBox").split())
    assert w > 0 and h > 0
    assert root.find("{http://www.w3.org/2000/svg}title") is not None
    for el in root.iter():
        for attr in ("fill", "stroke", "color", "stop-color"):
            if el.get(attr) is not None:
                assert _gray(el.get(attr)), f"{test}.svg: {el.tag} {attr}={el.get(attr)}"
        assert "style" not in el.attrib, f"{test}.svg: colors go in attributes, where this test checks them"
        assert not el.tag.endswith(("image", "script", "foreignObject")), el.tag


@pytest.mark.parametrize("test", forms.TESTS)
def test_a_pictures_labels_print_at_10_pt_or_more_on_the_form(test):
    root = ET.parse(IMG / f"{test}.svg").getroot()
    width = float(root.get("viewBox").split()[2])
    scale = forms.PICTURE_MM / width  # mm per unit, printed
    sizes = [float(el.get("font-size")) for el in root.iter() if el.get("font-size")]
    assert sizes
    assert min(sizes) * scale / PT >= 10.0
    assert f"flex: 0 0 {forms.PICTURE_MM}mm" in forms.form_html(test)


@pytest.mark.parametrize("test", forms.TESTS)
def test_the_docs_copy_of_a_picture_is_the_same_file(test):
    assert (DOCS_IMG / f"{test}.svg").read_bytes() == (IMG / f"{test}.svg").read_bytes(), \
        f"docs/img/measure/{test}.svg differs: copy scrimmage/measure/img/{test}.svg over it"


def test_the_pictures_are_one_per_test():
    assert sorted(p.stem for p in IMG.glob("*.svg")) == sorted(forms.TESTS)
    assert sorted(p.stem for p in DOCS_IMG.glob("*.svg")) == sorted(forms.TESTS)


# ------------------------------------------------------------------------------------------------------ the print

@pytest.mark.browser
@pytest.mark.parametrize("paper", ["Letter", "A4"])
def test_every_form_prints_on_one_page_with_room_to_spare(browser, paper):
    # 10 mm more bottom margin than the form asks for, so another browser's fonts have room
    tighter = "<style>@page { margin-bottom: 21mm; }</style></head>"
    page = browser.new_page()
    try:
        for test in forms.TESTS:
            page.set_content(forms.form_html(test).replace("</head>", tighter))
            pdf = page.pdf(format=paper)
            assert len(re.findall(rb"/Type\s*/Page\b", pdf)) == 1, f"the {test} form prints on more than one page"
    finally:
        page.close()


from browser_harness import browser  # noqa: E402,F401  (the headless Chromium fixture)


def _rects(root, fill):
    return [el for el in root.iter("{http://www.w3.org/2000/svg}rect") if el.get("fill") == fill]


def test_the_squeeze_pictures_ball_is_drawn_to_the_75_percent_gap():
    root = ET.parse(IMG / "squeeze.svg").getroot()
    top, bottom = sorted(_rects(root, "#cccccc"), key=lambda el: float(el.get("y")))
    gap = float(bottom.get("y")) - (float(top.get("y")) + float(top.get("height")))
    ball = next(root.iter("{http://www.w3.org/2000/svg}circle"))  # the ball before the squeeze
    assert 2 * float(ball.get("r")) * 0.75 == pytest.approx(gap)
    assert "ruler" in "".join(root.itertext())


def test_the_shot_pictures_path_rises_across_vertical_marks():
    root = ET.parse(IMG / "shot.svg").getroot()
    lines = list(root.iter("{http://www.w3.org/2000/svg}line"))
    marks = [el for el in lines if el.get("x1") == el.get("x2") and el.get("stroke") == "#555555"]
    assert len(marks) == 2
    path = next(el for el in lines if el.get("stroke-dasharray") == "2 5")
    assert float(path.get("y2")) < float(path.get("y1"))  # up and across


def test_the_shot_forms_spacing_example_gives_enough_frames_but_not_far_more():
    step = forms.TEST["shot"].steps[1]
    assert f"about {forms.TYPICAL_SPEED:g} m/s across, {forms.spacing_in(240)} in at 240 fps" in step
    frames = forms.spacing_in(240) * INCH * 240 / forms.TYPICAL_SPEED
    assert fit_launcher.GOOD_FRAMES <= frames <= 1.5 * fit_launcher.GOOD_FRAMES
    assert 24 * INCH * 960 / forms.TYPICAL_SPEED >= fit_launcher.GOOD_FRAMES  # the 960 fps example


def test_the_drop_picture_step_and_example_agree_on_the_bounce():
    root = ET.parse(IMG / "drop.svg").getroot()
    circles = list(root.iter("{http://www.w3.org/2000/svg}circle"))
    bounce = next(el for el in circles if el.get("stroke-dasharray"))
    bottom = float(bounce.get("cy")) + float(bounce.get("r"))
    floor, per_inch = 280.0, 11.0  # the picture's ruler: 0 at the floor line, 11 units to the inch
    example = next(r for r in forms.TEST["drop"].trial if r.name == "bounce").example
    assert (floor - bottom) / per_inch == pytest.approx(example)
    assert f"about {example:g} in up" in " ".join(forms.TEST["drop"].steps)
