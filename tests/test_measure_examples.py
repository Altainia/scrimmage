"""examples/measure: each measurement file loads, summarizes and fits as its README says, the launcher setups that
use them say so in their reports, and docs/measuring.md's quoted output is what `scrimmage measure` prints."""
import re
from pathlib import Path

import pytest

from scrimmage.__main__ import main
from scrimmage.launcher import report
from scrimmage.launcher.inputs import load_setup
from scrimmage.measure import cli, fit_cylinder, fit_launcher, forms
from scrimmage.measure.file import load
from scrimmage.measure.tests_direct import values

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "examples" / "measure"
DOC = ROOT / "docs" / "measuring.md"
FILES = sorted(HERE.glob("*.measure.toml"))


@pytest.fixture(scope="module")
def reports() -> dict[str, tuple[str, bool]]:
    """Each example file's `scrimmage measure` report and whether it is good enough to use (the fits run once)."""
    return {p.name: cli.report(load(p)) for p in FILES}


def test_there_is_one_example_of_each_kind():
    assert sorted(load(p).kind for p in FILES) == ["ball", "cylinder", "friction", "launcher"]


def test_every_example_file_is_good_enough_to_use(reports):
    for name, (text, good) in reports.items():
        assert good, name
        assert text.rstrip().endswith("Good enough to use: a run that names this file shows these numbers as "
                                      "measured."), name
        assert "warning: add who measured it" not in text, name


def test_the_fuel_ball(reports):
    text, _ = reports["fuel.measure.toml"]
    mf = load(HERE / "fuel.measure.toml")
    assert mf.base == "2026-fuel"
    v = values(mf)
    assert {k: s.n for k, (s, _) in v.items()} == {"mass": 5, "diameter": 5, "stiffness_n": 5, "restitution": 6}
    assert all(s.good for s, _ in v.values())
    assert "mass: average 215.6 g, lowest 214.0 g, highest 217.0 g, spread 1% (5 trials)" in text
    assert "diameter: average 150.2 mm (5.91 in)" in text
    assert "stiffness_n: average 123.1 N, lowest 119.2 N, highest 127.2 N, spread 7% (5 trials)" in text
    assert "restitution: average 0.595, lowest 0.553, highest 0.612, spread 10% (6 trials)" in text
    # the README's one flagged trial: flagged, not dropped, and the drop still good
    assert re.findall(r"warning: .*", text) == ["warning: trial 6 looks off: check it"]
    assert v["restitution"][0].outliers == [5]


def test_the_tread_on_fuel(reports):
    text, _ = reports["tread-on-fuel.measure.toml"]
    assert "friction: average 0.899, lowest 0.859, highest 0.939, spread 9% (5 trials)" in text
    assert load(HERE / "tread-on-fuel.measure.toml").settings["sled_setup"]["sled_g"] == 2270


def test_the_hooded_shots_fit_the_stiffness_well(reports):
    text, _ = reports["hooded-shots.measure.toml"]
    result = fit_launcher.fit(load(HERE / "hooded-shots.measure.toml"))
    assert result.fitted == ("stiffness_n",) and result.good
    assert result.warnings == [fit_launcher.SQUEEZE_SENTENCE]
    assert result.rms_error < fit_launcher.GOOD_RMS
    low, high = result.band
    assert (1 - fit_launcher.BAND) * result.stiffness_n <= low and high <= (1 + fit_launcher.BAND) * result.stiffness_n
    assert "fit: stiffness 91 N, anywhere from 81 to 103 N fits your shots" in text
    assert "the model misses your shots by 1.9% (root mean square)" in text
    # each shot spans 20 frames or more (the advice it follows), at two wheel speeds 10% or more apart
    mf = load(HERE / "hooded-shots.measure.toml")
    assert all(row["frames"] >= fit_launcher.GOOD_FRAMES for row in mf.trials["shot"])
    speeds = [row["wheel_rps"] for row in mf.trials["shot"]]
    assert max(speeds) >= fit_launcher.SECOND_SPEED * min(speeds)


def test_the_intake_deploy_fits_its_valve(reports):
    text, _ = reports["intake-deploy.measure.toml"]
    result = fit_cylinder.fit(load(HERE / "intake-deploy.measure.toml"))
    assert result.good and result.warnings == []
    assert "time: average 0.252 s, lowest 0.246 s, highest 0.258 s, spread 5% (5 trials)" in text
    assert "fit: valve conductance 0.38, anywhere from 0.37 to 0.39 fits your strokes (dm^3/(s bar))" in text
    assert "the model's stroke takes 0.252 s, off by +0.0%" in text


def test_a_launcher_setup_with_the_shots_shows_measured(tmp_path, capsys):
    main(["launcher", "run", str(HERE / "hooded-measured.toml"), "--out", str(tmp_path)])
    first = capsys.readouterr().out.splitlines()[0]
    assert first == ("UNCALIBRATED: still estimates: ball bounce, wheel friction. Measured: ball stiffness "
                     "(hooded-shots.measure.toml, J. Smith, 2026-11-12, 6 trials).")


def test_a_launcher_setup_with_the_ball_and_tread_is_calibrated():
    setup = load_setup(HERE / "fuel-shooter.toml")[0]
    assert report.calibration([setup]) == (
        "Calibrated from measurements: ball stiffness (fuel.measure.toml, J. Smith, 2026-11-10, 5 trials); ball "
        "bounce (fuel.measure.toml, J. Smith, 2026-11-10, 6 trials); wheel friction (tread-on-fuel.measure.toml, "
        "J. Smith, 2026-11-11, 5 trials).")
    assert setup.ball.stiffness_n == pytest.approx(123.1, abs=0.05)
    assert setup.wheels.friction == pytest.approx(0.899, abs=0.0005)


def test_the_measured_setup_differs_from_hooded_only_in_its_ball():
    def body(path):
        lines = path.read_text(encoding="utf-8").splitlines()
        return [ln.split("#")[0].rstrip() for ln in lines[lines.index("[wheels]"):]]
    assert body(HERE / "hooded-measured.toml") == body(ROOT / "examples" / "launcher" / "hooded.toml")


# --------------------------------------------------------------------------------------- docs/measuring.md

LINK = re.compile(r"\]\(([^)\s]+)\)")
FENCE = re.compile(r"```([a-z]*)\n(.*?)\n```", re.S)


def _slug(heading: str) -> str:
    text = re.sub(r"[`*]", "", heading.strip().lower())
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _anchors(md: Path) -> set[str]:
    text = FENCE.sub("", md.read_text(encoding="utf-8"))
    return {_slug(h) for h in re.findall(r"^#+ (.+)$", text, re.M)}


@pytest.mark.parametrize("md", [DOC, HERE / "README.md"], ids=lambda p: p.name)
def test_links_and_pictures_resolve(md):
    for target in LINK.findall(md.read_text(encoding="utf-8")):
        path, _, anchor = target.partition("#")
        dest = (md.parent / path).resolve() if path else md
        assert dest.exists(), f"{md.name}: {target} doesn't exist"
        if anchor:
            assert anchor in _anchors(dest), f"{md.name}: {target}: no heading with that anchor"


def test_every_test_has_its_section_picture_and_form():
    text = DOC.read_text(encoding="utf-8")
    for test in forms.TESTS:
        title = forms.TEST[test].title
        assert f"## {title}\n" in text, title
        assert f"](img/measure/{test}.svg)" in text, test
        assert f"scrimmage measure form {test} -o {test}-form.html" in text, test


def test_the_doc_s_quoted_output_is_what_scrimmage_measure_prints(tmp_path, reports):
    """Every line of the doc's output blocks is in a report: the examples', a two-trial squeeze, and the example's
    shots taken at 40 and 50 rps."""
    (tmp_path / "short.measure.toml").write_text(
        '[measure]\nkind = "ball"\nbase = "2026-fuel"\n\n[[squeeze]]\nforce_lb = 22.0\n\n[[squeeze]]\n'
        "force_lb = 28.6\n", encoding="utf-8")
    shots = (HERE / "hooded-shots.measure.toml").read_text(encoding="utf-8")
    shots = shots.replace('"../launcher/hooded.toml"', f'"{(ROOT / "examples" / "launcher" / "hooded.toml").as_posix()}"')
    for (rps, frames), (slow, slow_frames) in zip([(69.5, 25), (70.2, 25), (70.0, 24), (80.3, 25), (79.6, 24),
                                                   (80.1, 25)],
                                                  [(40.2, 38), (39.8, 38), (40.0, 37), (50.1, 30), (49.7, 31),
                                                   (50.0, 30)]):
        shots = shots.replace(f"wheel_rps = {rps}\nframes = {frames}\n", f"wheel_rps = {slow}\nframes = {slow_frames}\n",
                              1)
    (tmp_path / "slow.measure.toml").write_text(shots, encoding="utf-8")
    extra = [cli.report(load(tmp_path / name))[0] for name in ("short.measure.toml", "slow.measure.toml")]
    printed = "\n".join([*(text for text, _ in reports.values()), *extra])
    blocks = [body for lang, body in FENCE.findall(DOC.read_text(encoding="utf-8")) if lang == ""]
    assert len(blocks) >= 8
    for block in blocks:
        for line in block.splitlines():
            if line.strip() != "...":
                assert line in printed, line
