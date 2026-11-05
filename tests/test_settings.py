"""The user settings file (scrimmage/settings.py), `scrimmage --units`, and /api/settings."""
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from scrimmage import settings
from scrimmage.settings import Settings, current_units, load, save, set_override, settings_dir


def test_tests_never_see_the_users_own_settings(tmp_path):
    # the conftest fixture points $SCRIMMAGE_CONFIG at a scratch folder: the product default applies
    assert Path(os.environ["SCRIMMAGE_CONFIG"]) == settings_dir()
    assert Path.home() / ".config" not in settings_dir().parents
    assert load() == Settings(units="imperial")
    assert current_units() == "imperial"


def test_settings_dir_per_os(monkeypatch, tmp_path):
    monkeypatch.delenv("SCRIMMAGE_CONFIG")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert settings_dir() == tmp_path / ".config" / "scrimmage"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert settings_dir() == tmp_path / "xdg" / "scrimmage"
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/ignored")      # the XDG spec: a relative path is ignored
    assert settings_dir() == tmp_path / ".config" / "scrimmage"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert settings_dir() == tmp_path / "Library" / "Application Support" / "scrimmage"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert settings_dir() == tmp_path / "Roaming" / "scrimmage"
    monkeypatch.delenv("APPDATA")
    assert settings_dir() == tmp_path / "AppData" / "Roaming" / "scrimmage"
    monkeypatch.setenv("SCRIMMAGE_CONFIG", str(tmp_path / "mine"))
    assert settings_dir() == tmp_path / "mine"


def test_save_then_load(units):
    save(units="metric")
    assert (settings_dir() / "settings.toml").read_text() == 'units = "metric"\n'
    assert load().units == "metric" and current_units() == "metric"
    save(units="imperial")
    assert load().units == "imperial"


def test_save_keeps_comments_and_settings_it_doesnt_know():
    f = settings_dir() / "settings.toml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('# my settings\nunits = "imperial"  # FRC is\nlanguage = "en-GB"\n')
    save(units="metric")
    assert f.read_text() == '# my settings\nunits = "metric"  # FRC is\nlanguage = "en-GB"\n'
    assert [p.name for p in f.parent.iterdir()] == ["settings.toml"]     # no temporary file left behind


def test_save_is_atomic(monkeypatch):
    save(units="metric")
    f = settings_dir() / "settings.toml"

    def broken(src, dst):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        save(units="imperial")
    assert f.read_text() == 'units = "metric"\n'                         # the old file is whole
    assert [p.name for p in f.parent.iterdir()] == ["settings.toml"]


def test_save_refuses_a_bad_value_or_an_unknown_setting():
    with pytest.raises(ValueError, match='units must be "imperial" or "metric", not "furlongs"'):
        save(units="furlongs")
    with pytest.raises(ValueError, match="unknown setting 'colour'"):
        save(colour="blue")
    assert not (settings_dir() / "settings.toml").exists()


# Review Focus 3: a settings file that is missing, unreadable or has a bad value falls back to imperial with one
# plain warning, never a crash
def test_a_missing_file_is_the_default_without_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert load() == Settings(units="imperial")
        assert settings.problem() is None
    assert not caplog.records


@pytest.mark.parametrize("text,why", [
    ('units = "furlongs"\n', 'units = "furlongs" isn\'t imperial or metric'),
    ("units = 3\n", "units = 3 isn't imperial or metric"),
    ("units = metric\n", "isn't valid TOML"),
    (b"\xff\xfe", "isn't valid TOML"),
])
def test_a_bad_file_falls_back_to_imperial_with_one_warning(caplog, text, why):
    f = settings_dir() / "settings.toml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(text if isinstance(text, bytes) else text.encode())
    with caplog.at_level(logging.WARNING, logger="scrimmage.settings"):
        for _ in range(3):
            assert load() == Settings(units="imperial")
            assert current_units() == "imperial"
    warnings = [r.getMessage() for r in caplog.records]
    assert len(warnings) == 1, warnings
    assert why in warnings[0] and str(f) in warnings[0] and "using imperial" in warnings[0]
    assert settings.problem() == warnings[0]
    # fixing the file clears it, and saving over a broken file writes a good one
    save(units="metric")
    assert load().units == "metric" and settings.problem() is None


def test_an_unreadable_file_falls_back(caplog):
    (settings_dir() / "settings.toml").mkdir(parents=True)        # a folder where the file should be
    with caplog.at_level(logging.WARNING, logger="scrimmage.settings"):
        assert load() == Settings(units="imperial")
    assert len(caplog.records) == 1 and "can't read" in caplog.records[0].getMessage()


def test_the_value_is_read_case_insensitively():
    f = settings_dir() / "settings.toml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('units = "Metric"\n')
    assert load().units == "metric" and settings.problem() is None


def test_load_rereads_the_file_when_it_changes():
    save(units="metric")
    assert load().units == "metric"
    (settings_dir() / "settings.toml").write_text('units = "imperial"\n')   # changed outside: another editor, by hand
    assert load().units == "imperial"


def test_override_wins_over_the_file(units):
    units("metric")
    set_override("imperial")
    assert current_units() == "imperial" and load().units == "metric"
    set_override(None)
    assert current_units() == "metric"
    with pytest.raises(ValueError):
        set_override("cubits")


def test_the_units_fixture_sets_the_system(units):
    assert current_units() == "imperial"
    units("metric")
    assert current_units() == "metric"


# -- scrimmage --units -----------------------------------------------------------------------------------------------
def test_cli_units_overrides_the_file(monkeypatch, units):
    from scrimmage import __main__ as cli
    units("metric")
    seen = []
    monkeypatch.setattr(cli, "_java", lambda args: seen.append(current_units()))
    cli.main(["--units", "imperial", "java", "path"])
    cli.main(["--units=metric", "java", "path"])
    set_override(None)
    cli.main(["java", "path"])                    # without it, the settings file
    assert seen == ["imperial", "metric", "metric"]


def test_cli_units_before_measure(monkeypatch):
    from scrimmage import __main__ as cli
    from scrimmage.measure import cli as measure_cli
    seen = []
    monkeypatch.setattr(measure_cli, "main", lambda argv: seen.append((argv, current_units())) or 0)
    with pytest.raises(SystemExit):
        cli.main(["--units", "metric", "measure", "form", "ball"])
    assert seen == [(["form", "ball"], "metric")]


def test_cli_units_refuses_another_value(capsys):
    from scrimmage import __main__ as cli
    with pytest.raises(SystemExit) as e:
        cli.main(["--units", "cubits", "java", "path"])
    assert e.value.code == 2
    assert "--units" in capsys.readouterr().err and settings._override is None


# -- /api/settings ---------------------------------------------------------------------------------------------------
@pytest.fixture
def api():
    from scrimmage.assembler.server import App
    app = App(None, open_browser=False)
    url = app.start()
    base, token = url.split("#")[0].rstrip("/"), url.split("#token=")[1]

    def call(method, body=None, token=token):
        req = urllib.request.Request(base + "/api/settings", method=method,
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Scrimmage-Token": token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    yield call
    app.stop()


def test_api_get_and_post(api):
    st, r = api("GET")
    assert st == 200 and r["units"] == "imperial" and r["warning"] is None
    assert r["table"]["units"]["metric"]["length"]["name"] == "mm"
    st, r = api("POST", {"units": "metric"})
    assert st == 200 and r["units"] == "metric" and r["table"]["kinds"]
    assert load().units == "metric"
    assert api("GET")[1]["units"] == "metric"
    # an edit on disk (another editor, the CLI, by hand) shows on the next GET
    (settings_dir() / "settings.toml").write_text('units = "imperial"\n')
    assert api("GET")[1]["units"] == "imperial"


def test_api_refuses_a_bad_value(api):
    st, r = api("POST", {"units": "cubits"})
    assert st == 400 and r["error"] == 'units must be "imperial" or "metric", not "cubits"'
    st, r = api("POST", {})
    assert st == 400 and "units" in r["error"]
    assert api("GET", token="wrong")[0] == 403
    assert not (settings_dir() / "settings.toml").exists()


def test_api_reports_a_bad_file(api):
    f = settings_dir() / "settings.toml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("units = [\n")
    st, r = api("GET")
    assert st == 200 and r["units"] == "imperial" and "isn't valid TOML" in r["warning"]


def test_api_post_moves_the_cli_override(api, units):
    # `scrimmage --units imperial edit`: the menu still switches, for this run and in the file
    set_override("imperial")
    st, r = api("POST", {"units": "metric"})
    assert r["units"] == "metric" and current_units() == "metric" and load().units == "metric"
