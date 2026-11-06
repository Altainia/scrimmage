"""docs/settings.md's Language and Theme sections and docs/translating.md, as the code has them: the menu's languages
are i18n.languages(), the themes settings.py's and the page's, the file's keys settings.py's, `--language` the
CLI's, and the guide's commands, file format, contexts and gate tests the tool's and the tests' (like
test_settings_docs.py for Units)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from scrimmage import i18n, i18n_tool, settings

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "docs" / "settings.md"
GUIDE = ROOT / "docs" / "translating.md"
STATIC = ROOT / "scrimmage" / "assembler" / "static"


def flat(s: str) -> str:
    return " ".join(s.split())


def test_the_menus_languages_and_themes_are_the_codes():
    doc = flat(SETTINGS.read_text(encoding="utf-8"))
    names = " | ".join(i18n.languages().values())
    assert f"**Language: {names}.** English (US) is the default" in doc
    assert settings.Settings().language == i18n.SOURCE == "en-US"
    assert "**Theme: System | Light | Dark.** System is the default" in doc
    assert settings.THEMES == ("system", "light", "dark") and settings.Settings().theme == "system"
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="settings-language-label" data-i18n="Language">Language<' in html
    assert re.findall(r'data-setting="theme" data-value="(\w+)"', html) == list(settings.THEMES)
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    for word in ("System", "Light", "Dark"):
        assert f't("{word}", {{ context: "theme" }})' in app, word
    assert "[translating.md](translating.md)" in doc and "`scrimmage/locales/`" in doc


def test_the_settings_file_block_is_valid():
    doc = SETTINGS.read_text(encoding="utf-8")
    block = tomllib.loads(re.search(r"```toml\n(.*?)```", doc, re.S).group(1))
    assert settings.check_units(block["units"]) == "metric"
    assert settings.check_language(block["language"]) == "en-GB"
    assert settings.check_theme(block["theme"]) == "dark"
    assert set(block) == {f.name for f in settings.dataclasses.fields(settings.Settings)}


def test_language_on_the_command_line():
    out = subprocess.run([sys.executable, "-m", "scrimmage", "--help"], capture_output=True, text=True,
                         check=True).stdout
    assert "--language {" + ",".join(i18n.languages()) + "}" in out
    assert "stay American English" in flat(out)
    doc = flat(SETTINGS.read_text(encoding="utf-8"))
    assert "`scrimmage --language en-GB <command>`" in doc
    assert "scrimmage --language en-GB edit configs/kitbot.toml" in doc and (ROOT / "configs" / "kitbot.toml").is_file()
    assert "Theme has no command-line flag" in doc and "--theme" not in out


def test_the_known_gaps_are_the_rulings():
    doc = flat(SETTINGS.read_text(encoding="utf-8"))
    for gap in ("keep their outcomes and grip-loss words in American English",
                "**The command line is American English throughout**",
                "shows session and config errors", "**The Part tab's build messages**",
                "**A Test-tab sweep or floor spread that fails as a whole**",
                "**The robot code panel's Build now result**", "**Why a scenario has no robot**",
                "**A few warnings from a running simulation**", "**MCP results stay American English**",
                "**Older browsers** (before Chrome 123, Safari 17.5 or Firefox 120) show the light theme only"):
        assert gap in doc, gap
    css = (STATIC / "app.css").read_text(encoding="utf-8")
    assert "@supports (color: light-dark(" in css


def test_the_guide_is_the_tools_and_the_files():
    doc = GUIDE.read_text(encoding="utf-8")
    # the commands, which the CLI takes
    assert "scrimmage i18n template --out scrimmage/locales/fr.json" in doc and "scrimmage i18n check fr" in doc
    for args in (["i18n", "template", "--help"], ["i18n", "check", "--help"]):
        out = subprocess.run([sys.executable, "-m", "scrimmage", *args], capture_output=True, text=True, check=True)
        assert "usage:" in out.stdout
    # the file format: _language, the plural key, the context key, the en-GB file's size
    gb = json.loads((ROOT / "scrimmage" / "locales" / "en-GB.json").read_text(encoding="utf-8"))
    assert gb["_language"] == "English (UK)" and '"_language": "Français"' in doc
    assert len(gb) - 1 < 100 and "fewer than 100 entries" in doc
    assert i18n.PLURAL == "|" and '"{count} part|{count} parts": {"one":' in doc
    assert i18n.CONTEXT == "\u0004" and '"list separator\\u0004 and "' in doc
    assert "`PLURAL_RULES` in `scrimmage/i18n.py`" in doc and hasattr(i18n, "PLURAL_RULES")
    assert "Intl.PluralRules" in (STATIC / "i18n.js").read_text(encoding="utf-8")
    for form in ("zero", "one", "two", "few", "many", "other"):
        assert f"`{form}`" in doc
    # every context the code uses is in the Contexts section
    sources, _ = i18n_tool.scan()
    used = {s.key.split(i18n.CONTEXT)[0] for s in sources if i18n.CONTEXT in s.key}
    section = doc.split("## Contexts")[1].split("\n## ")[0]
    assert used <= set(re.findall(r"`([a-z ]+)`", section)), used
    # the gates it names exist
    for test in re.findall(r"`(tests/test_\w+\.py)`", doc):
        assert (ROOT / test).is_file(), test
    assert "`tests/test_i18n_sweep.py`" in doc and "`tests/test_en_gb_catalog.py`" in doc
    assert "`--language fr`" in doc and "scrimmage --language fr edit" in doc


def test_the_readme_points_at_both():
    readme = flat((ROOT / "README.md").read_text(encoding="utf-8"))
    assert "`scrimmage --language en-GB edit <file>`" in readme
    assert "[docs/translating.md](docs/translating.md)" in readme and "docs/settings.md#language" in readme
    assert "**Theme: System**" in readme
