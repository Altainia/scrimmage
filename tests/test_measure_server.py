"""The Measure tab's server side: a measurement file (`<name>.measure.toml`) behind /api/measure/*, the blank forms at
/forms/<test>.html, and "Measure something" on the start page (/api/app/new kind "measure")."""

import math
import tomllib
import urllib.request

import pytest

from measure_helpers import WITH_BALL, ball_file, measure_file, frames, synthetic
from scrimmage.assembler import recent
from scrimmage.assembler.server import App, classify
from scrimmage.measure import cli, fit_cylinder, fit_launcher, forms, stats
from scrimmage.measure.file import KIND_TESTS, load
from test_assembler_server import Client

BALL = """\
# the practice bin's ball, measured after kickoff
[measure]
kind = "ball"
about = "a FUEL ball from the practice bin"
by = "J. Smith"
date = 2026-11-10
tools = "bathroom scale, two boards"  # the blue scale
base = "2026-fuel"

[squeeze_setup]
tare_lb = 1.0

# the first squeeze slipped a little
[[squeeze]]
force_lb = 18.8

[[squeeze]]
force_lb = 19.0  # pressed slowly
"""


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_SKIP_JAVA", "1")
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def measure_app():
    started = []

    def make(path=None):
        app = App(path, open_browser=False)
        started.append(app)
        return Client(app, app.start())
    yield make
    for a in started:
        a.stop()


def _ball(tmp_path, text=BALL, name="fuel.measure.toml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _state(c, since=None):
    s, b = c.get("/api/measure/state" + (f"?since={since}" if since is not None else ""))
    assert s == 200, b
    return b


def _trial(cells, origin=None, extra=None):
    return {"origin": origin, "cells": cells, "extra": extra or {}}


def _force(lb):
    return {"force": {"unit": "lb", "value": lb}}


# --------------------------------------------------------------------------------------------------------- opening

def test_a_measure_file_is_classified_by_its_name_or_its_measure_table(tmp_path):
    assert classify(_ball(tmp_path)) == "measure"
    other = tmp_path / "ball.toml"
    other.write_text(BALL, encoding="utf-8")
    assert classify(other) == "measure"
    broken = tmp_path / "broken.measure.toml"
    broken.write_text('[measure]\nkind = "ball\n', encoding="utf-8")
    assert classify(broken) == "measure"


@pytest.mark.parametrize("kind", list(KIND_TESTS))
def test_the_server_opens_each_kind_and_lists_its_tests(tmp_path, measure_app, kind):
    p = tmp_path / f"{kind}.measure.toml"
    p.write_text(forms.starter_toml(KIND_TESTS[kind][0]), encoding="utf-8")
    c = measure_app(p)
    s, app = c.get("/api/app/state")
    assert s == 200 and app["mode"] == "measure" and app["path"] == str(p.resolve())
    st = _state(c)
    assert st["kind"] == kind and st["file_error"] is None
    assert [t["name"] for t in st["tests"]] == list(KIND_TESTS[kind])
    for t in st["tests"]:
        form = forms.TEST[t["name"]]
        # the readings come from the same list the printed forms use
        assert [r["name"] for r in t["trial"]] == [r.name for r in form.trial]
        assert [r["name"] for r in t["setup"]] == [r.name for r in form.setup]
        assert t["steps"] == list(form.steps) and t["picture"].startswith("<svg")
        assert t["fit"] == (t["name"] in ("shot", "stroke"))
    # the other routes refuse while it's open
    assert c.get("/api/robot/state")[0] == 404 and c.get("/api/test/state")[0] == 404
    # opening it again from the start page remembers it as a measurement
    assert c.post("/api/app/open", {"path": str(p)})[0] == 200
    assert recent.recent()[0] == {**recent.recent()[0], "path": str(p.resolve()), "kind": "measure"}


def test_each_reading_offers_the_units_the_file_reads(tmp_path, measure_app):
    c = measure_app(_ball(tmp_path))
    squeeze = next(t for t in _state(c)["tests"] if t["name"] == "squeeze")
    force = squeeze["trial"][0]
    assert [u["unit"] for u in force["units"]] == ["n", "lb", "kg"]
    assert [u["label"] for u in force["units"]] == ["N", "lb", "kg"]
    tare = squeeze["setup"][0]
    assert [u["unit"] for u in tare["units"]] == ["n", "lb", "kg", "g", "oz"]
    stroke = forms.TEST["stroke"]
    p = tmp_path / "deploy.measure.toml"
    p.write_text(forms.starter_toml("stroke"), encoding="utf-8")
    c.post("/api/app/open", {"path": str(p)})
    st = _state(c)
    by_name = {r["name"]: r for r in st["tests"][0]["trial"] + st["tests"][0]["setup"]}
    assert [u["unit"] for u in by_name["frames"]["units"]] == ["frames"]
    assert by_name["vertical"]["type"] == "bool" and by_name["working_psi"]["type"] == "number"
    assert by_name["vertical"]["units"] == [] and len(by_name) == len(stroke.trial) + len(stroke.setup)


def test_the_state_splits_each_trial_into_its_readings(tmp_path, measure_app):
    c = measure_app(_ball(tmp_path))
    st = _state(c)
    f = st["file"]
    assert f["measure"] == {"about": "a FUEL ball from the practice bin", "by": "J. Smith", "date": "2026-11-10",
                            "tools": "bathroom scale, two boards", "base": "2026-fuel"}
    sq = f["tests"]["squeeze"]
    assert sq["setup"] == {"cells": {"tare": {"unit": "lb", "value": 1.0}}, "extra": {}}
    assert sq["trials"] == [_trial(_force(18.8), 0), _trial(_force(19.0), 1)]
    assert f["tests"]["size"] == {"setup": {"cells": {}, "extra": {}}, "trials": []}
    assert "2026-fuel" in st["bases"]


# --------------------------------------------------------------------------------------------------------- summary

def _file(st, trials, test="squeeze", setup=None):
    f = st["file"]
    tests = dict(f["tests"])
    tests[test] = {"setup": setup or tests[test]["setup"], "trials": trials}
    return {**f, "tests": tests}


def test_the_summary_is_the_stats_of_the_trials_typed_in(tmp_path, measure_app):
    c = measure_app(_ball(tmp_path))
    st = _state(c)
    lbs = [18.8, 19.0, 19.2, 18.9, 19.1]
    s, b = c.call("POST", "/api/measure/summary", {"file": _file(st, [_trial(_force(x)) for x in lbs])})
    assert s == 200, b
    sq = b["tests"]["squeeze"]
    assert sq["error"] is None
    (field,) = sq["fields"]
    want = stats.summary([(x - 1.0) * 4.4482216152605 for x in lbs])
    assert field["field"] == "stiffness_n" and math.isclose(field["mean"], want.mean)
    assert field["n"] == 5 and field["good"] and field["why"] == "" and field["outliers"] == []
    assert field["shown"]["mean"] == cli.FIELD["stiffness_n"][1](want.mean)
    assert [r["trial"] for r in field["rows"]] == [0, 1, 2, 3, 4]
    # nothing was written
    assert tomllib.loads((tmp_path / "fuel.measure.toml").read_text(encoding="utf-8"))["squeeze"][0]["force_lb"] == 18.8


def test_a_trial_that_looks_off_is_flagged_and_kept(tmp_path, measure_app):
    c = measure_app(_ball(tmp_path))
    st = _state(c)
    lbs = [18.8, 19.0, 19.2, 18.9, 19.1, 30.0]
    _, b = c.call("POST", "/api/measure/summary", {"file": _file(st, [_trial(_force(x)) for x in lbs])})
    (field,) = b["tests"]["squeeze"]["fields"]
    assert field["outliers"] == [5] and field["n"] == 6
    assert [r["outlier"] for r in field["rows"]] == [False] * 5 + [True]
    assert "trial 6 looks off: check it" in field["warnings"]
    assert not field["good"] and "disagree" in field["why"]


def test_a_reading_problem_names_the_trial_without_the_path(tmp_path, measure_app):
    c = measure_app(_ball(tmp_path))
    st = _state(c)
    trials = [_trial(_force(19.0)), _trial({}), _trial(_force(19.1))]
    _, b = c.call("POST", "/api/measure/summary", {"file": _file(st, trials)})
    err = b["tests"]["squeeze"]["error"]
    assert err.startswith("[[squeeze]] trial 2 has no force reading"), err
    assert str(tmp_path) not in err


def test_a_size_trial_that_only_weighs_lines_up_with_its_row(tmp_path, measure_app):
    c = measure_app(_ball(tmp_path))
    st = _state(c)
    tape = {"circumference": {"unit": "in", "value": 18.5}}
    rows = [_trial({"mass": {"unit": "g", "value": m}, **(tape if d else {})})
            for m, d in ((142, True), (143, False), (141, True))]
    _, b = c.call("POST", "/api/measure/summary", {"file": _file(st, rows, test="size")})
    fields = {f["field"]: f for f in b["tests"]["size"]["fields"]}
    assert [r["trial"] for r in fields["mass"]["rows"]] == [0, 1, 2]
    assert [r["trial"] for r in fields["diameter"]["rows"]] == [0, 2]
    # a diameter far off in the last of 7 trials (trial 2 only weighs): flagged on its own row, named trial 7
    tapes = [18.5, None, 18.6, 18.4, 18.5, 18.6, 24.0]
    rows = [_trial({"mass": {"unit": "g", "value": 216},
                    **({"circumference": {"unit": "in", "value": c}} if c else {})}) for c in tapes]
    _, b = c.call("POST", "/api/measure/summary", {"file": _file(st, rows, test="size")})
    (dia,) = [f for f in b["tests"]["size"]["fields"] if f["field"] == "diameter"]
    assert dia["outliers"] == [6] and [r["trial"] for r in dia["rows"] if r["outlier"]] == [6]
    assert "trial 7 looks off: check it" in dia["warnings"]


def test_a_stroke_file_summarizes_its_times(tmp_path, measure_app):
    p = tmp_path / "deploy.measure.toml"
    p.write_text('[measure]\nkind = "cylinder"\nby = "J. Smith"\ndate = 2026-11-10\n\n[stroke_setup]\n'
                 "bore_in = 1.0625\nrod_in = 0.3125\nstroke_in = 6\nfps = 240\n\n[[stroke]]\ntime_s = 0.31\n\n"
                 "[[stroke]]\nframes = 75\n\n[[stroke]]\ntime_s = 0.30\n", encoding="utf-8")
    c = measure_app(p)
    (field,) = _state(c)["summary"]["stroke"]["fields"]
    assert field["field"] == "time" and field["n"] == 3
    assert [r["shown"] for r in field["rows"]] == ["0.310 s", "0.312 s", "0.300 s"]


# ------------------------------------------------------------------------------------------------------------ save

def test_saving_round_trips_and_keeps_the_comments(tmp_path, measure_app):
    p = _ball(tmp_path)
    c = measure_app(p)
    st = _state(c)
    f = st["file"]
    f["measure"]["by"] = "A. Lee"
    f["measure"]["tools"] = ""
    sq = f["tests"]["squeeze"]
    sq["trials"][1]["cells"]["force"]["value"] = 19.4
    sq["trials"].append(_trial({"force": {"unit": "lb", "value": 19.2}}))
    sq["setup"]["cells"]["tare"] = {"unit": "kg", "value": 0.45}
    f["tests"]["drop"]["setup"] = {"cells": {"drop": {"unit": "in", "value": 18},
                                             "floor": {"unit": "", "value": "concrete"}}, "extra": {}}
    f["tests"]["drop"]["trials"] = [_trial({"bounce": {"unit": "in", "value": 6.5}})]
    s, b = c.call("POST", "/api/measure/save", {"file": f, "version": st["version"]})
    assert s == 200, b
    text = p.read_text(encoding="utf-8")
    for comment in ("# the practice bin's ball", "# the blue scale", "# the first squeeze slipped", "# pressed slowly"):
        assert comment in text, text
    assert "force_lb = 18.8\n" in text   # an unchanged value is left as it was written
    mf = load(p)
    assert mf.by == "A. Lee" and mf.tools == "" and mf.base == "2026-fuel" and mf.date == "2026-11-10"
    assert [r["force_lb"] for r in mf.trials["squeeze"]] == [18.8, 19.4, 19.2]
    assert mf.settings["squeeze_setup"] == {"tare_kg": 0.45}
    assert mf.settings["drop_setup"] == {"drop_in": 18, "floor": "concrete"}
    assert mf.trials["drop"] == [{"bounce_in": 6.5}]
    new = _state(c)
    assert new["version"] > st["version"] and new["file"] == {**f, "tests": {**f["tests"], "squeeze": {
        **sq, "trials": [{**t, "origin": i} for i, t in enumerate(sq["trials"])]}, "drop": {
        **f["tests"]["drop"], "trials": [{**f["tests"]["drop"]["trials"][0], "origin": 0}]}}}


def test_removing_a_trial_keeps_the_others_comments(tmp_path, measure_app):
    p = _ball(tmp_path)
    c = measure_app(p)
    st = _state(c)
    f = st["file"]
    f["tests"]["squeeze"]["trials"] = f["tests"]["squeeze"]["trials"][1:]
    s, b = c.call("POST", "/api/measure/save", {"file": f, "version": st["version"]})
    assert s == 200, b
    text = p.read_text(encoding="utf-8")
    assert "18.8" not in text and "force_lb = 19.0  # pressed slowly" in text
    # removing every trial leaves the test out
    f["tests"]["squeeze"]["trials"] = []
    s, b = c.call("POST", "/api/measure/save", {"file": f, "version": _state(c)["version"]})
    assert s == 200, b
    assert "squeeze" not in load(p).trials


def test_a_new_unit_renames_the_key_in_place(tmp_path, measure_app):
    p = _ball(tmp_path)
    c = measure_app(p)
    st = _state(c)
    f = st["file"]
    f["tests"]["squeeze"]["trials"][1]["cells"]["force"]["unit"] = "kg"
    s, b = c.call("POST", "/api/measure/save", {"file": f, "version": st["version"]})
    assert s == 200, b
    assert "force_kg = 19.0  # pressed slowly" in p.read_text(encoding="utf-8")


def test_readings_the_tab_has_no_column_for_are_kept(tmp_path, measure_app):
    p = _ball(tmp_path, BALL.replace("force_lb = 18.8\n", "force_lb = 18.8\ntare_lb = 1.2\n"))
    c = measure_app(p)
    st = _state(c)
    first = st["file"]["tests"]["squeeze"]["trials"][0]
    assert first["extra"] == {"tare_lb": 1.2}
    first["cells"]["force"]["value"] = 18.6
    s, b = c.call("POST", "/api/measure/save", {"file": st["file"], "version": st["version"]})
    assert s == 200, b
    assert load(p).trials["squeeze"][0] == {"force_lb": 18.6, "tare_lb": 1.2}


def test_a_save_that_isnt_a_measurement_file_is_refused(tmp_path, measure_app):
    p = _ball(tmp_path)
    before = p.read_text(encoding="utf-8")
    c = measure_app(p)
    st = _state(c)
    f = st["file"]
    f["measure"]["date"] = "next tuesday"
    s, b = c.call("POST", "/api/measure/save", {"file": f, "version": st["version"]})
    assert s == 400 and "date must be a date like 2026-11-10" in b["error"], b
    s, b = c.call("POST", "/api/measure/save", {"file": {"tests": []}, "version": st["version"]})
    assert s == 400, b
    assert p.read_text(encoding="utf-8") == before


def test_a_save_after_an_outside_edit_is_refused_with_the_new_state(tmp_path, measure_app):
    p = _ball(tmp_path)
    c = measure_app(p)
    st = _state(c)
    p.write_text(BALL.replace("19.0", "19.5"), encoding="utf-8")
    new = _state(c, st["version"])   # the long poll sees the outside edit
    assert new["file"]["tests"]["squeeze"]["trials"][1]["cells"]["force"]["value"] == 19.5
    s, b = c.call("POST", "/api/measure/save", {"file": st["file"], "version": st["version"]})
    assert s == 409 and b["state"]["version"] == new["version"], b
    assert b["errors"] == [{"field": "status", "message": b["error"]}]   # the page's api() keeps a body with errors
    assert "19.5" in p.read_text(encoding="utf-8")


def test_a_file_that_isnt_a_measurement_file_opens_read_only_with_why(tmp_path, measure_app):
    p = _ball(tmp_path, BALL + "\n[spin]\nrps = 3\n")
    c = measure_app(p)
    st = _state(c)
    assert "unknown table 'spin'" in st["file_error"] and st["file"] is None
    assert [t["name"] for t in st["tests"]] == ["size", "squeeze", "drop"]
    s, b = c.call("POST", "/api/measure/save", {"file": {}, "version": st["version"]})
    assert s == 409 and "unknown table 'spin'" in b["error"], b


# ------------------------------------------------------------------------------------------------------------- fit

def test_fit_returns_the_launcher_fit_the_cli_prints(tmp_path, measure_app):
    mf = synthetic(tmp_path, 120.0, 0.95, [50.0, 80.0])
    c = measure_app(mf.path)
    st = _state(c)
    s, b = c.call("POST", "/api/measure/fit", {"file": st["file"], "test": "shot"})
    assert s == 200, b
    want = fit_launcher.fit(mf)
    text, _ = cli.report(mf)
    assert b["band_text"] == want.band_text and f"fit: {want.band_text}" in text
    assert b["what"] == "the ball's stiffness" and b["held"] == "the wheel friction held at 0.95"
    assert math.isclose(b["value"], want.stiffness_n) and b["good"] is want.good and b["why"] == want.why
    assert math.isclose(b["rms_error"], want.rms_error) and b["warnings"] == want.warnings
    assert [(r["measured"], r["model"]) for r in b["rows"]] == [tuple(x) for x in want.per_shot]
    assert all(math.isclose(r["off"], (r["model"] - r["measured"]) / r["measured"]) for r in b["rows"])


def test_fit_says_why_a_fit_at_the_edge_isnt_good(tmp_path, measure_app):
    mf = measure_file(tmp_path, [(50.0, frames(1.5)), (80.0, frames(2.0))])
    c = measure_app(mf.path)
    s, b = c.call("POST", "/api/measure/fit", {"file": _state(c)["file"], "test": "shot"})
    assert s == 200, b
    assert not b["good"] and "at the edge of what it tries" in b["why"]


def test_fit_with_the_squeeze_measured_fits_the_wheel_friction(tmp_path, measure_app):
    ball_file(tmp_path, [118.0, 120.0, 122.0])
    mf = synthetic(tmp_path, 120.0, 1.1, [50.0, 80.0], header=WITH_BALL)
    c = measure_app(mf.path)
    s, b = c.call("POST", "/api/measure/fit", {"file": _state(c)["file"], "test": "shot"})
    assert s == 200, b
    assert b["what"] == "the wheel's friction" and math.isclose(b["value"], fit_launcher.fit(mf).friction)


def test_fit_returns_the_cylinder_fit_the_cli_prints(tmp_path, measure_app):
    p = tmp_path / "deploy.measure.toml"
    p.write_text('[measure]\nkind = "cylinder"\nby = "J. Smith"\ndate = 2026-11-10\n\n[stroke_setup]\n'
                 "bore_in = 1.0625\nrod_in = 0.3125\nstroke_in = 6\nload_mass_lb = 2\nfps = 240\n\n"
                 "[[stroke]]\ntime_s = 0.31\n\n[[stroke]]\nframes = 75\n\n[[stroke]]\ntime_s = 0.30\n",
                 encoding="utf-8")
    c = measure_app(p)
    s, b = c.call("POST", "/api/measure/fit", {"file": _state(c)["file"], "test": "stroke"})
    assert s == 200, b
    want = fit_cylinder.fit(load(p))
    assert b["band_text"] == want.band_text and math.isclose(b["conductance"], want.conductance)
    assert math.isclose(b["model_time"], want.model_time) and math.isclose(b["measured_time"], want.measured_time)
    assert math.isclose(b["off"], want.error)
    assert b["good"] is want.good and b["why"] == want.why and b["warnings"] == want.warnings


def test_fit_problems_are_plain_errors(tmp_path, measure_app):
    mf = synthetic(tmp_path, 120.0, 0.95, [50.0, 80.0])
    c = measure_app(mf.path)
    f = _state(c)["file"]
    f["tests"]["shot"]["setup"]["cells"]["setup"]["value"] = "gone.toml"
    s, b = c.call("POST", "/api/measure/fit", {"file": f, "test": "shot"})
    assert s == 400 and "gone.toml" in b["error"] and str(tmp_path / "shooter.measure.toml") not in b["error"], b
    s, b = c.call("POST", "/api/measure/fit", {"file": f, "test": "size"})
    assert s == 400 and "fit" in b["error"], b


# ----------------------------------------------------------------------------------------------- forms and new files

def test_the_blank_form_page_is_the_printable_form(tmp_path, measure_app):
    c = measure_app(_ball(tmp_path))
    for test in forms.TESTS:
        with urllib.request.urlopen(f"{c.base}/forms/{test}.html") as r:   # a plain link: no token needed
            assert r.status == 200 and r.headers.get_content_type() == "text/html"
            assert r.read().decode("utf-8") == forms.form_html(test)
    assert c.get("/forms/spin.html")[0] == 404


def test_measure_something_makes_a_starter_file_and_opens_it(tmp_path, measure_app):
    c = measure_app()
    s, b = c.call("POST", "/api/app/new", {"kind": "measure", "path": "fuel", "measure": "ball"})
    assert s == 200, b
    p = tmp_path / "fuel.measure.toml"
    assert p.read_text(encoding="utf-8").startswith(forms.starter_toml("size").splitlines()[0])
    assert load(p).kind == "ball"
    assert c.get("/api/app/state")[1]["mode"] == "measure" and _state(c)["kind"] == "ball"
    # a name already ending in .measure.toml (or .toml) isn't doubled; an existing file is refused
    s, b = c.call("POST", "/api/app/new", {"kind": "measure", "path": "deploy.measure.toml", "measure": "cylinder"})
    assert s == 200 and (tmp_path / "deploy.measure.toml").is_file(), b
    s, b = c.call("POST", "/api/app/new", {"kind": "measure", "path": "fuel.toml", "measure": "ball"})
    assert s == 400 and "already exists" in b["error"], b
    s, b = c.call("POST", "/api/app/new", {"kind": "measure", "path": "x", "measure": "spin"})
    assert s == 400 and "ball, friction, launcher or cylinder" in b["error"], b


def test_a_starter_saved_with_its_date_blank_is_refused_and_blank_lines_arent_added(tmp_path, measure_app):
    p = tmp_path / "fuel.measure.toml"
    p.write_text('[measure]\nkind = "ball"\nby = "J. Smith"\ndate = 2026-11-10\n', encoding="utf-8")
    c = measure_app(p)
    st = _state(c)
    f = st["file"]
    f["measure"]["date"] = ""
    s, b = c.call("POST", "/api/measure/save", {"file": f, "version": st["version"]})
    assert s == 400 and "date must be a date like 2026-11-10" in b["error"], b
    # about and tools left blank aren't added; a blank name is written (by is required), and the file still reads
    f["measure"].update(date="2026-11-10", by="")
    f["tests"]["squeeze"]["trials"] = [_trial(_force(19.0))]
    s, b = c.call("POST", "/api/measure/save", {"file": f, "version": st["version"]})
    assert s == 200, b
    head = tomllib.loads(p.read_text(encoding="utf-8"))["measure"]
    assert "about" not in head and "tools" not in head and head["by"] == ""
    assert load(p).trials["squeeze"] == [{"force_lb": 19.0}]
    # the starter's own blank lines stay as they were
    starter = tmp_path / "new.measure.toml"
    starter.write_text(forms.starter_toml("squeeze"), encoding="utf-8")
    c.post("/api/app/open", {"path": str(starter)})
    st = _state(c)
    st["file"]["tests"]["squeeze"]["trials"] = [_trial(_force(19.0))]
    s, b = c.call("POST", "/api/measure/save", {"file": st["file"], "version": st["version"]})
    assert s == 200, b
    head = tomllib.loads(starter.read_text(encoding="utf-8"))["measure"]
    assert head["about"] == "" and head["tools"] == "" and head["by"] == ""


def test_a_save_that_cant_be_written_says_so(tmp_path, measure_app, monkeypatch):
    p = _ball(tmp_path)
    c = measure_app(p)
    st = _state(c)

    def refuse(path, text):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr("scrimmage.assembler.measure_session.replace_text", refuse)
    s, b = c.call("POST", "/api/measure/save", {"file": st["file"], "version": st["version"]})
    assert s == 400 and b["error"] == "can't write fuel.measure.toml: Permission denied", b
    assert p.read_text(encoding="utf-8") == BALL


def test_the_measure_routes_need_the_token(tmp_path, measure_app):
    c = measure_app(_ball(tmp_path))
    assert c.get("/api/measure/state", token=None)[0] == 403
    for route in ("summary", "fit", "save"):
        assert c.call("POST", f"/api/measure/{route}", {}, token=None)[0] == 403
        assert c.call("POST", f"/api/measure/{route}", {}, token="wrong")[0] == 403


def test_the_start_page_is_told_the_kinds_and_a_bad_kind_is_refused(tmp_path, measure_app):
    c = measure_app()
    kinds = c.get("/api/app/state")[1]["measure_kinds"]
    assert [k["kind"] for k in kinds] == list(KIND_TESTS)
    assert kinds[0]["tests"] == [forms.TEST[t].title for t in KIND_TESTS["ball"]]
    for bad in ("spin", None, 3):
        s, b = c.call("POST", "/api/app/new", {"kind": "measure", "path": "x", **({"measure": bad} if bad else {})})
        assert s == 400 and "ball, friction, launcher or cylinder" in b["error"], b
    assert not list(tmp_path.glob("*.measure.toml"))
