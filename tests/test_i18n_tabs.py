"""The Scenario, Bench and Measure tabs' server words in another language (sub-project 20b, Task 5): check messages
as shown, report.md, the run's notes, the "fix the robot first" error (a code, spec L3), the Bench headlines and
summary.txt, the gallery, and the Measure tab's test words and printed forms follow the Language setting; machine
text (report.json, the MCP scenario report and bench headlines) stays byte for byte American English (spec L2).

The catalog is these tests' own (a scratch locales folder), so it can change words en-GB spells the same."""
import json
from types import SimpleNamespace

import pytest

from scrimmage import i18n

GB = {
    "_language": "English (UK)",
    "touched {target} at t={t}": "{target} was touched at t={t}",
    "never touched {target}": "{target} was never touched",
    "fix the robot first: {why}": "sort the robot out first: {why}",
    "PASS: {scenario}": "PASSED: {scenario}",
    "FAIL: {scenario}": "FAILED: {scenario}",
    "Note: {note}": "N.B. {note}",
    "this scenario has no checks, so passing only means the robot program ran: add a check (where the robot ends "
    "up, say) to test something": "no checks here, so a pass only means the programme ran",
    "step interrupted at t={t}": "the step stopped at t={t}",
    "Robot program": "Robot programme",
    "the robot program exited (code {code})": "the robot programme stopped (code {code})",
    "{name} ({mode}, t={start}–{end} s)": "{name}: {mode}, from {start} s to {end} s",
    "{out} of {n} left through {outlet} at {speed} {unit} ± {spread}, {way}":
        "{out}/{n} out of {outlet}: {speed} {unit} ± {spread}, {way}",
    "Pieces": "The pieces",
    "Energy check: ok (a failure means the simulation is wrong, not the design)": "Energy check: fine",
    "Ball squeeze": "Ball squash",
    "the scale's reading, pressed": "the scale's figure, pressed down",
    "Gives {what}.": "It gives {what}.",
    "Every idea": "All the ideas",
}


@pytest.fixture
def gb(tmp_path, monkeypatch, language):
    """The Language setting en-GB, read from a scratch locales folder holding GB."""
    folder = tmp_path / "locales"
    folder.mkdir()
    (folder / "en-GB.json").write_text(json.dumps(GB, ensure_ascii=False), encoding="utf-8")
    shipped = i18n.LOCALES
    monkeypatch.setattr(i18n, "LOCALES", folder)
    i18n.reload()
    language("en-GB")
    yield
    monkeypatch.setattr(i18n, "LOCALES", shipped)
    i18n.reload()


# ----------------------------------------------------------------------------------------- checks and report.md

def _touch(target="hub", t=1.25):
    from scrimmage.checks import ContactCheck
    world = SimpleNamespace(contact_names=lambda: ["hub", "wall"])
    check = ContactCheck(target, True, world, "s.toml, step 1, must_touch")
    check.first_t = t
    return check.end(None)


def test_a_checks_shown_words_follow_the_language_and_its_message_doesnt(gb):
    r = _touch()
    assert r.shown() == "hub was touched at t=1.25"
    assert r.message == "touched hub at t=1.25"                    # report.json's and MCP's, as main wrote it
    assert r.to_dict()["message"] == "touched hub at t=1.25"
    assert r.to_dict(shown=True)["message"] == "hub was touched at t=1.25"
    with i18n.fixed():
        assert r.shown() == "touched hub at t=1.25"                # the CLI's terminal, and en-US


def _report():
    from scrimmage.checks import CheckResult, said
    from scrimmage.scenario_runner import NO_CHECKS_NOTE, Report, StepReport, _interrupted, _said
    steps = [StepReport("drive", "auto", 0.0, 3.0, checks=[_touch()]),
             StepReport("shoot", "teleop", 3.0, 4.5, interrupted=_interrupted(4.5))]
    implicit = [said("robot program running", False, "the robot program exited (code 1)",
                     lambda: i18n.t("the robot program exited (code {code})", code=1)),
                CheckResult("no crash in console", True, "no stack trace in the console")]
    return Report(scenario="s", description="", steps=steps, implicit=implicit, notes=[_said(NO_CHECKS_NOTE)])


def test_report_md_is_in_the_language_and_report_json_is_mains(gb, language, tmp_path):
    gb_dir, us_dir = tmp_path / "gb", tmp_path / "us"
    gb_dir.mkdir()
    us_dir.mkdir()
    rep = _report()
    rep.write(gb_dir)
    md = (gb_dir / "report.md").read_text()
    assert md.startswith("# FAILED: s\n")
    assert "> N.B. no checks here, so a pass only means the programme ran" in md
    assert "## drive: auto, from 0.00 s to 3.00 s" in md and "- ✅ hub was touched at t=1.25" in md
    assert "- ❌ the step stopped at t=4.500" in md
    assert "## Robot programme" in md and "- ❌ the robot programme stopped (code 1)" in md
    # the page's copy (runs.py: to_dict(shown=True)) is in the language too
    shown = rep.to_dict(shown=True)
    assert shown["notes"] == ["no checks here, so a pass only means the programme ran"]
    assert shown["steps"][1]["interrupted"] == "the step stopped at t=4.500"
    language("en-US")
    _report().write(us_dir)
    # report.json: American English, byte for byte, whatever the setting (it is also the MCP run_scenario report)
    assert (gb_dir / "report.json").read_bytes() == (us_dir / "report.json").read_bytes()
    machine = json.loads((us_dir / "report.json").read_text())
    assert machine["steps"][0]["checks"][0]["message"] == "touched hub at t=1.25"
    assert machine["steps"][1]["interrupted"] == "step interrupted at t=4.500"
    assert machine["notes"][0].startswith("this scenario has no checks")
    assert all(type(n) is str for n in rep.to_dict()["notes"])     # plain text, not the worded kind
    us = (us_dir / "report.md").read_text()
    assert us.startswith("# FAIL: s\n") and "## drive (auto, t=0.00–3.00 s)" in us and "touched hub at t=1.25" in us


def test_the_cli_summary_is_american_english(gb):
    with i18n.fixed():
        text = _report().summary()
    assert "touched hub at t=1.25" in text and "step interrupted at t=4.500" in text


# ----------------------------------------------------------------------------------------- fix the robot first

def test_fix_the_robot_first_is_a_code_and_follows_the_language(gb, language, tmp_path):
    """L3: the server finds the error by its code, not its words, and says it again in its own words; a language
    change words the same errors again without a new version."""
    from scrimmage.assembler import scenario_validate
    from scrimmage.assembler.server import App
    from test_assembler_server import Client
    p = tmp_path / "s.toml"
    p.write_text('config = "nowhere.toml"\n\n[[step]]\nmode = "auto"\nduration = 1.0\n')
    res = scenario_validate.check(p.read_text(), p)
    assert res.errors[0].code == scenario_validate.ROBOT_FIRST
    assert res.errors[0].message.startswith("sort the robot out first: ")
    app = App(p, open_browser=False, runs_dir=tmp_path / "runs")
    try:
        c = Client(app, app.start())
        s = c.get("/api/scenario/state")[1]
        assert s["read_only_reason"].startswith("sort the robot out first: ") and "nowhere.toml" in s["robot_problem"]
        assert s["errors"][0]["message"] == s["read_only_reason"]
        language("en-US")
        s2 = c.get("/api/scenario/state")[1]
        assert s2["version"] == s["version"]
        assert s2["read_only_reason"].startswith("fix the robot first: ")
        assert s2["errors"][0]["message"] == s2["read_only_reason"]
    finally:
        app.stop()


# ----------------------------------------------------------------------------------------------------- bench

@pytest.fixture(scope="module")
def pinch():
    from pathlib import Path
    from scrimmage.bench.run import run_bench
    from scrimmage.bench.spec import load_bench
    return run_bench(load_bench(Path(__file__).resolve().parent.parent / "examples" / "bench" / "pinch_car.toml"))


def test_bench_headlines_and_summary_follow_the_language_and_mcps_dont(gb, language, pinch, tmp_path, units):
    from scrimmage.bench.report import headlines, write_results
    from scrimmage.mcp_server import _machine_headlines
    units("metric")
    lines = headlines(pinch)
    assert lines[0].startswith("3/3 out of `shot`: ") and " m/s ± " in lines[0], lines
    write_results(pinch, tmp_path / "gb")
    summary = (tmp_path / "gb" / "summary.txt").read_text()
    assert lines[0] in summary and "\nThe pieces\n" in summary and "Energy check: fine" in summary
    machine = _machine_headlines(pinch)
    language("en-US")
    assert _machine_headlines(pinch) == machine == headlines(pinch)   # MCP's: main's words whatever the setting
    assert machine[0].startswith("3 of 3 left through `shot` at ")


def test_the_gallery_is_in_the_language(gb, pinch):
    from pathlib import Path
    from scrimmage.bench.gallery import Entry, gallery_html
    html = gallery_html(Path("kickoff"), [Entry(file=Path("pinch_car.toml"), result=pinch)])
    assert '<html lang="en-GB">' in html and "<h2>All the ideas</h2>" in html and "3/3 out of <code>shot</code>" in html


# --------------------------------------------------------------------------------------------------- measure

def test_the_measure_tabs_test_words_and_forms_follow_the_language(gb, language):
    from scrimmage.assembler.measure_session import test_json
    from scrimmage.measure.forms import TEST, form_html
    t = test_json("squeeze")
    assert t["title"] == "Ball squash" and t["trial"][0]["what"] == "the scale's figure, pressed down"
    assert TEST["squeeze"].title == "Ball squeeze"      # the data itself stays the source (N_)
    form = form_html("squeeze")
    assert "<h1>Ball squash</h1>" in form and "It gives the ball's stiffness" in form
    language("en-US")
    assert test_json("squeeze")["title"] == "Ball squeeze"   # cached per language
    assert "<h1>Ball squeeze</h1>" in form_html("squeeze")


def test_the_shipped_en_gb_catalog_in_the_bench_files(language, pinch, tmp_path):
    from pathlib import Path
    from scrimmage.bench.gallery import Entry, gallery_html
    language("en-GB")
    html = gallery_html(Path("kickoff"), [Entry(file=Path("pinch_car.toml"), result=pinch)])
    assert "grey still inside" in html and "gray" not in html
