"""The rest of the server text the web app shows, in another language (sub-project 20b, Task 6c): the bench problems,
the Measure tab's fits and averages, part.toml, [partner], [field] and [range] errors, the CAD import, the bridge's and
the robot-code checklist's notes. Each area follows the Language setting, and nothing reads their words back (spec
L3): the Bench tab takes a run's headlines from summary.json, a CAD material error carries its field, and the
checklist keeps a stopped step's advice without splitting the sentence. A web-app scenario run writes report.json in
American English, as MCP's does (spec L2).

The catalog is these tests' own (a scratch locales folder), so it can change words en-GB spells the same."""
import json
import re
from pathlib import Path

import pytest

from scrimmage import i18n

GB = {
    "_language": "English (UK)",
    # bench
    "{table} {key} must be positive, not {value}": "in {table}, {key} has to be above nought (it is {value})",
    "{kind} '{name}' and {other_kind} '{other}' overlap by {depth} mm": "{kind} '{name}' runs {depth} mm into "
                                                                      "{other_kind} '{other}'",
    "UNCALIBRATED: {text}": "NOT YET MEASURED: {text}",
    "Energy check: ok (a failure means the simulation is wrong, not the design)": "Energy: sound",
    # measure
    "{count} trial: take at least {min}|{count} trials: take at least {min}": {
        "one": "only {count} go: do {min} or more", "other": "only {count} goes: do {min} or more"},
    "{path}: needs a {measure_table} table saying what was measured (kind, by, date)":
        "{path}: say what was measured in a {measure_table} table (kind, by, date)",
    "stroke time": "time for a stroke",
    # parts, partner, field, range
    "{key} must be greater than 0, got {value}": "{key} has to be above nought, not {value}",
    "{field_table} obstacle must be true or false": "{field_table} obstacle is true or false, nothing else",
    "{partner_table} must be a table": "{partner_table} has to be a table",
    "{range_table}: give ball or piece, not both": "{range_table}: a ball or a piece, not the pair",
    # CAD: the density error's words no longer say "density"
    "A custom material needs a density above zero (kg per cubic meter): enter one.":
        "Give the custom material its weight per volume (kg per cubic metre), above nought.",
    "{name} is no longer in the CAD": "{name} has gone from the CAD",
    # bridge, robot code
    "fast mode, but the robot program created Phoenix 6 devices ({devices}): they force real time; use the "
    "fast-mode IO for them": "fast mode, yet the programme made Phoenix 6 devices ({devices})",
    "Phoenix 6 simulation runs on the wall clock, so this robot runs in real time (not lockstep)":
        "Phoenix 6 keeps to the wall clock, so this robot runs in real time",
    "This took too long and was stopped. {advice}": "{advice} (It was stopped: it took too long.)",
    "It needs Java (WPILib) and the internet the first time. The last lines are below.":
        "The first time, it wants Java (WPILib) and the internet. See the last lines.",
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


# ------------------------------------------------------------------------------------------------------------ bench

PINCH = Path(__file__).resolve().parent.parent / "examples" / "bench" / "pinch_car.toml"


def _bad_bench(tmp_path: Path) -> Path:
    text = PINCH.read_text(encoding="utf-8")
    bad = re.sub(r"(?m)^width = ([0-9.]+)", "width = -1.0", text, count=1)
    assert bad != text
    f = tmp_path / "bad.toml"
    f.write_text(bad, encoding="utf-8")
    return f


def test_a_bench_problem_follows_the_language_and_keeps_its_key(gb, tmp_path):
    from scrimmage.bench.problems import collect_problems
    f = _bad_bench(tmp_path)
    _, problems = collect_problems(f)
    (p,) = [p for p in problems if p.level == "error"]
    assert re.fullmatch(r"in \[\[wheel\]\] '\w+', width has to be above nought \(it is -1\.0\)", p.message), p
    assert p.key.endswith(".width")
    with i18n.fixed():
        _, problems = collect_problems(f)
    (us,) = [p for p in problems if p.level == "error"]
    assert re.fullmatch(r"\[\[wheel\]\] '\w+' width must be positive, not -1\.0", us.message), us
    assert us.key == p.key


def test_the_bench_tab_reads_a_runs_headlines_by_position_not_their_words(gb, tmp_path):
    """summary.txt in another language (its UNCALIBRATED and Energy check lines translated) still gives the Bench tab
    its headlines and the energy flag: summary.json counts the headline lines and holds the flags, no words."""
    from scrimmage.assembler.bench_session import _energy_failed, _summary
    from scrimmage.measure.labels import marked
    unc = marked("estimates")
    assert unc == "NOT YET MEASURED: estimates"
    out = tmp_path / "r"
    out.mkdir()
    (out / "summary.txt").write_text(f"b (A)\n\n3 of 3 out\n{unc}\nwarning: w\n\nEnergy: sound\n",
                                     encoding="utf-8")
    # an older folder, no summary.json: the block after the title by position; no energy flag
    assert _summary(out) == (["3 of 3 out", unc, "warning: w"], "warning: w") and _energy_failed(out) is False
    (out / "summary.json").write_text(json.dumps({"headlines": 2, "uncalibrated": True, "energy_failed": True}),
                                      encoding="utf-8")
    assert _summary(out) == (["3 of 3 out", unc], unc)
    assert _energy_failed(out) is True
    with i18n.fixed():
        assert marked("estimates") == "UNCALIBRATED: estimates"


def test_write_results_writes_summary_json_without_words(gb, tmp_path, units):
    """summary.json is machine JSON: the same bytes in any Language and Units setting (summary.txt follows them)."""
    from scrimmage.assembler.bench_session import _summary
    from scrimmage.bench.report import headlines, write_results
    from scrimmage.bench.run import run_bench
    from scrimmage.bench.spec import load_bench
    units("metric")
    result = run_bench(load_bench(PINCH))
    out = write_results(result, tmp_path / "r")
    heads = headlines(result)
    data = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    title = data.pop("title_lines")
    assert data == {"headlines": len(heads), "uncalibrated": True, "energy_failed": False}
    assert (out / "summary.txt").read_text(encoding="utf-8").splitlines()[title:title + 2] == ["", heads[0]]
    assert heads[-1].startswith("NOT YET MEASURED: ")
    assert _summary(out) == (heads, heads[-1])
    summary = (out / "summary.txt").read_text(encoding="utf-8")
    assert "Energy: sound" in summary
    data = (out / "summary.json").read_bytes()
    units("imperial")
    with i18n.fixed():
        other = write_results(result, tmp_path / "us")
    assert (other / "summary.json").read_bytes() == data


# ---------------------------------------------------------------------------------------------------------- measure

def test_the_measure_words_follow_the_language(gb, tmp_path):
    from scrimmage.assembler.measure_session import _field_json
    from scrimmage.measure.file import MeasureError, parse
    from scrimmage.measure.stats import summary, why
    s = summary([1.0, 1.1])
    assert why(s) == "only 2 goes: do 3 or more"
    with pytest.raises(MeasureError) as e:
        parse({}, tmp_path / "fuel.measure.toml")
    assert str(e.value).endswith(": say what was measured in a [measure] table (kind, by, date)")
    assert _field_json("time", summary([0.3, 0.31, 0.3]), [0.3, 0.31, 0.3])["what"] == "time for a stroke"
    with i18n.fixed():
        assert why(s) == "2 trials: take at least 3"


# --------------------------------------------------------------------------------------- part, partner, field, range

def test_part_partner_field_and_range_errors_follow_the_language(gb):
    from scrimmage.parts.field import add_field
    from scrimmage.parts.model import PartError, part_from_dict
    from scrimmage.parts.partner import parse_partner
    from scrimmage.testrange.build import parse_range_section
    part = {"part": {"name": "p", "format": 1}, "body": [{"name": "b", "mass": -2.0}]}
    with pytest.raises(PartError) as e:
        part_from_dict(part, "p/part.toml", None)
    assert str(e.value).endswith("mass has to be above nought, not -2.0"), e.value
    with pytest.raises(PartError, match=r"^\[field\] obstacle is true or false, nothing else$"):
        add_field(None, {"obstacle": 1})
    with pytest.raises(PartError, match=r"^\[partner\] has to be a table$"):
        parse_partner({"partner": 3})
    with pytest.raises(PartError, match=r"^\[range\]: a ball or a piece, not the pair$"):
        parse_range_section({"range": {"file": "2026", "ball": "fuel", "piece": "fuel"}}, None)


# -------------------------------------------------------------------------------------------------------------- CAD

def test_a_cad_density_error_lands_on_its_field_whatever_its_words(gb):
    """cad/edits.py used to pick custom_density by finding "density" in the message (L3); the catalog's words
    here don't say it, and the field is still custom_density."""
    from scrimmage.cad import CadError
    from scrimmage.cad.mass import density
    with pytest.raises(CadError) as e:
        density("custom", custom=-1.0)
    assert e.value.field == "custom_density" and "density" not in str(e.value)
    with pytest.raises(CadError) as e:
        density("pla", infill=2.0)
    assert e.value.field == "infill"


def test_a_cad_edit_names_custom_density_in_any_language(gb):
    from scrimmage.cad.edits import EditError, apply_edit
    from scrimmage.cad.frame import RobotFrame
    from scrimmage.cad.settings import BodySettings, ImportFile, PartSettings, PieceSettings
    imp = ImportFile(name="bot", import_id="x", source="bot.step", units="m", frame=RobotFrame(),
                     pieces={"a1": PieceSettings("base")}, bodies={"base": BodySettings(part="bot")},
                     parts={"bot": PartSettings("bot")})
    with pytest.raises(EditError) as e:
        apply_edit(imp, "set_material", {"pieces": ["a1"], "material": "custom", "custom_density": -1})
    assert e.value.field == "custom_density"
    assert e.value.message.startswith("Give the custom material its weight per volume")


def test_a_cad_reimport_note_follows_the_language(gb):
    from scrimmage.cad.settings import BodySettings, ImportFile, PartSettings, PieceSettings, reimport
    from scrimmage.cad.frame import RobotFrame
    from scrimmage.cad.source import Source
    imp = ImportFile(name="bot", import_id="x", source="bot.step", units="m", frame=RobotFrame(),
                     pieces={"gone#1": PieceSettings("base")}, bodies={"base": BodySettings(part="bot")},
                     parts={"bot": PartSettings("bot")})
    _, messages = reimport(imp, Source(kind="step", path=Path("bot.step"), units="m", pieces=[]))
    assert "gone#1 has gone from the CAD" in messages


# ------------------------------------------------------------------------------------------------ bridge, robot code

def test_the_bridge_notes_follow_the_language(gb):
    from scrimmage import bridge
    assert bridge.fast_phoenix_message(["Talon FX (v6)[1]"]) == \
        "fast mode, yet the programme made Phoenix 6 devices (Talon FX (v6)[1])"
    b = bridge.Bridge.__new__(bridge.Bridge)
    b.phoenix, b.real_ds = True, False
    assert b.realtime_reason == "Phoenix 6 keeps to the wall clock, so this robot runs in real time"
    with i18n.fixed():
        assert b.realtime_reason == bridge.REALTIME_REASON


def test_a_stopped_fix_keeps_its_advice_without_splitting_a_sentence(gb):
    """robotcode._failed took the advice after the first ". " of the message; in a language whose sentences break
    elsewhere that cut the wrong words. The advice is now its own text."""
    from scrimmage import robotcode
    c = robotcode._failed("agent", lambda: i18n.t("Building scrimmage's Java helper didn't work."),
                          lambda: i18n.t("It needs Java (WPILib) and the internet the first time. The last lines are "
                                         "below."), [], "build_agent", robotcode.TIMED_OUT)
    assert c.message == "The first time, it wants Java (WPILib) and the internet. See the last lines. (It was " \
                        "stopped: it took too long.)"
    with i18n.fixed():
        c = robotcode._failed("agent", lambda: i18n.t("Building scrimmage's Java helper didn't work."),
                              lambda: i18n.t("It needs Java (WPILib) and the internet the first time. The last lines "
                                             "are below."), [], "build_agent", 1)
    assert c.message == ("Building scrimmage's Java helper didn't work. It needs Java (WPILib) and the internet the "
                         "first time. The last lines are below.")
