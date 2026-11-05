"""docs/settings.md: the Units setting as the code has it. The table is units.py's, the menu's name and choices are
the page's, the default, the file and its folder are settings.py's, `--units` is the CLI's, the typing examples
parse and the refusal is units.js's, and the README, the assembler doc, the measure examples and the MCP
instructions point at it."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scrimmage import mcp_server, settings, units

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "settings.md"
STATIC = ROOT / "scrimmage" / "assembler" / "static"

# the table's first column, by the kind each row is
ROWS = {"Small length": "length", "Field length": "field", "Speed": "speed", "Mass": "mass", "Force": "force",
        "Torque": "torque", "Pressure": "pressure", "Density": "density", "Spring rate": "spring"}
SAME = ("angle", "time", "current", "voltage")


def text() -> str:
    return DOC.read_text(encoding="utf-8")


def flat(s: str) -> str:
    return " ".join(s.split())


def test_the_table_is_the_codes():
    rows = re.findall(r"^\| ([^|]+?) \| ([^|]+?) \| ([^|]+?) \|$", text(), re.M)
    got = {}
    for name, imperial, metric in rows:
        kind = next((k for words, k in ROWS.items() if name.startswith(words)), None)
        if kind:
            got[kind] = (imperial, metric)
    assert got == {k: (units.unit_name(k, "imperial"), units.unit_name(k, "metric")) for k in ROWS.values()}
    assert set(units.KINDS) == set(ROWS.values()) | set(SAME)
    for k in SAME:                          # the same in both, as the doc says
        assert units.unit_name(k, "imperial") == units.unit_name(k, "metric")
    doc = flat(text())
    assert ("Angles are degrees, time is seconds, current is amps (A) and voltage is volts (V) in both" in doc)


def test_the_examples_read_as_the_formatter_writes_them():
    doc = flat(text())
    for si, kind, imperial, metric in ((0.1016, "length", "4 in", "101.6 mm"), (0.00635, "length", "0.25 in", "6.4 mm"),
                                       (3.81, "field", "12.5 ft", "3.81 m"),
                                       (60 * units.LBF / units.IN ** 2, "pressure", "60 psi", "413.7 kPa")):
        assert units.fmt(si, kind, "imperial") == imperial and units.fmt(si, kind, "metric") == metric
        assert f"**{imperial}** or **{metric}**" in doc, imperial


def test_the_setting_is_the_menus_and_imperial_by_default():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<span class="settings-label" id="settings-units-label">Units</span>' in html
    choices = re.findall(r'data-setting="units" data-value="(\w+)"[^>]*>(\w+)<', html)
    assert choices == [("imperial", "Imperial"), ("metric", "Metric")]
    assert tuple(v for v, _ in choices) == settings.SYSTEMS
    assert "**Units: Imperial | Metric.** Imperial is the default" in text()
    assert settings.load().units == "imperial"          # no settings file (conftest): the default
    assert '>Settings</button>' in html and "**Settings** button" in text()


def test_the_file_and_its_folder_are_settings_pys(monkeypatch, tmp_path):
    doc = text()
    block = re.search(r"```toml\n(.*?)```", doc, re.S).group(1)
    assert settings.check_units(tomllib.loads(block)["units"]) == "metric"
    assert settings.FILE == "settings.toml" and "`settings.toml`" in doc
    assert "Set `SCRIMMAGE_CONFIG` to a folder" in doc
    monkeypatch.setenv("SCRIMMAGE_CONFIG", str(tmp_path))
    assert settings.settings_dir() == tmp_path
    monkeypatch.delenv("SCRIMMAGE_CONFIG")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("APPDATA", "APPDATA")
    home = Path.home()
    for platform, shown, want in (("linux", "~/.config/scrimmage/", home / ".config" / "scrimmage"),
                                  ("darwin", "~/Library/Application Support/scrimmage/",
                                   home / "Library" / "Application Support" / "scrimmage"),
                                  ("win32", "%APPDATA%\\scrimmage\\", Path("APPDATA") / "scrimmage")):
        monkeypatch.setattr(sys, "platform", platform)
        assert settings.settings_dir() == want, platform
        assert f"`{shown}`" in doc, shown
    assert "`$XDG_CONFIG_HOME/scrimmage/`" in doc


def test_typing_is_units_js():
    doc = flat(text())
    for typed in ("4 in", '4"', "100mm", "12.5 ft", "10 fps", "2 Nm", "0.0975 lb/in^3"):
        assert f"`{typed}`" in doc, typed
        word = re.sub(r"^[\d.]+\s*", "", typed)
        assert any(word.lower() in [a.lower() for a in names] for names in units.ALIASES.values()), typed
    for system, words in (("imperial", ("in", "ft")), ("metric", ("mm", "cm", "m"))):
        assert tuple(units.LENGTHS[system]) == words
    js = (STATIC / "units.js").read_text(encoding="utf-8")
    template = re.search(r"error: `(Units is set to .*?)`", js).group(1)
    said = (template.replace("${SYSTEM_NAME[sys]}", "Imperial").replace("${u.name}", "ft").replace("${o}", "m"))
    assert f'"{said}"' in doc
    assert "takes `6 in`" in doc and "takes `50 cm` or `500 mm`" in doc


def test_units_on_the_command_line(tmp_path):
    out = subprocess.run([sys.executable, "-m", "scrimmage", "--help"], capture_output=True, text=True,
                         check=True).stdout
    assert "--units {imperial,metric}" in out
    doc = text()
    assert "`scrimmage --units imperial <command>` or `scrimmage --units metric <command>`" in doc
    assert "scrimmage --units metric measure examples/measure/fuel.measure.toml" in doc
    run = subprocess.run([sys.executable, "-m", "scrimmage", "--units", "metric", "measure",
                          str(ROOT / "examples" / "measure" / "fuel.measure.toml")], capture_output=True, text=True,
                         cwd=ROOT)
    assert "diameter: average 150.2 mm" in run.stdout, run.stdout + run.stderr


def test_the_field_grid_is_the_drawings():
    js = (STATIC / "fieldpick.js").read_text(encoding="utf-8")
    assert '{ step: 5 * 0.3048, size: 5, unit: "ft" } : { step: 1, size: 1, unit: "m" }' in js
    assert "draws its grid every 5 ft in Imperial" in flat(text()) and "every 1 m in Metric" in flat(text())


def test_machine_output_is_what_the_code_keeps():
    from test_units_machine_text import MAIN
    doc = flat(text())
    assert MAIN["drive"]["outcome"].endswith("m in 0.5 s (went 0.43 m)")
    assert "(and meters for a drive's distance)" in doc and "in inches and psi as they always were" in doc
    assert '("pose: 0.40 m from target")' in doc
    assert units.MACHINE == "imperial"
    for known in ("**The Test tab's sweep and floor spread tables**", "**The launcher model's own output**",
                  "**A bench sweep's From and To boxes**", "**Changing Units clears the Test tab's undo history**",
                  "**Another open tab or editor**"):
        assert known in doc, known


@pytest.mark.parametrize("md", ["README.md", "docs/assembler.md", "examples/measure/README.md"])
def test_the_other_docs_point_here(md):
    assert "settings.md" in (ROOT / md).read_text(encoding="utf-8")


def test_the_readme_and_the_agents_are_told():
    readme = flat((ROOT / "README.md").read_text(encoding="utf-8"))
    assert "`scrimmage --units metric <command>`" in readme
    assert "MCP results, `report.json` and JSON and CSV numbers keep fixed units whatever the setting" in readme
    assert "Tool results are in fixed units whatever the web app's Units setting" in readme
    assert "Results are in fixed units whatever the web app's Units setting" in mcp_server.INSTRUCTIONS
