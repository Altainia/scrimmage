"""The translation calls (scrimmage/i18n.py), the Language setting, `scrimmage --language`, the machine guard, and
the `scrimmage i18n template|check` tool (scrimmage/i18n_tool.py)."""
from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

from scrimmage import i18n, i18n_tool, settings, units
from scrimmage.i18n import t, tn

ROOT = Path(__file__).resolve().parent.parent


# entries of the shipped en-GB catalog that sources use (cad/checklist.py's plural, the Part tab's label)
CG, CG_GB = "Center of gravity", "Centre of gravity"
PIECES = ("{count} piece in {body} is still guessed as aluminum",
          "{count} pieces in {body} are still guessed as aluminum")

@pytest.fixture
def locales(tmp_path, monkeypatch):
    """locales({"fr": {...}}) writes those catalogs in a scratch locales folder and makes it the one i18n reads."""
    folder = tmp_path / "locales"
    folder.mkdir()

    def make(catalogs: dict[str, object]) -> Path:
        for tag, catalog in catalogs.items():
            text = catalog if isinstance(catalog, str) else json.dumps(catalog, ensure_ascii=False)
            (folder / f"{tag}.json").write_text(text, encoding="utf-8")
        monkeypatch.setattr(i18n, "LOCALES", folder)
        i18n.reload()
        return folder
    yield make
    monkeypatch.undo()
    i18n.reload()


# -- t and tn (Review Focus 1) ---------------------------------------------------------------------------------------
def test_untranslated_text_is_the_source():
    assert i18n.current_language() == "en-US"
    assert t("Color") == "Color"
    assert t("Saved {name}", name="intake") == "Saved intake"
    assert t("A literal {{brace}} and {x}", x=3) == "A literal {brace} and 3"
    assert t("Not in any catalog", lang="en-GB") == "Not in any catalog"


def test_the_shipped_en_gb_catalog():
    assert i18n.languages() == {"en-US": "English (US)", "en-GB": "English (UK)"}
    assert t(CG, lang="en-GB") == CG_GB
    assert t(CG) == CG
    assert tn(*PIECES, 1, lang="en-GB", body="arm") == "1 piece in arm is still guessed as aluminium"
    assert tn(*PIECES, 2.5, lang="en-GB", body="arm") == "2.5 pieces in arm are still guessed as aluminium"


def test_placeholders_can_be_reordered(locales):
    locales({"fr": {"_language": "Français", "{who} moved {what}": "{what} déplacé par {who}"}})
    assert t("{who} moved {what}", who="Ana", what="the arm") == "Ana moved the arm"
    assert t("{who} moved {what}", lang="fr", who="Ana", what="le bras") == "le bras déplacé par Ana"


def test_a_missing_placeholder_value_is_a_bug():
    with pytest.raises(KeyError, match="name"):
        t("Saved {name}")


def test_english_plurals():
    for n, want in ((0, "0 parts"), (1, "1 part"), (2, "2 parts"), (1.5, "1.5 parts"), (-1, "-1 parts")):
        assert tn("{count} part", "{count} parts", n) == want, n
    assert tn("one ball in {where}", "{count} balls in {where}", 1, where="the hopper") == "one ball in the hopper"
    assert [i18n.plural_category("en-US", n) for n in (0, 1, 2)] == ["other", "one", "other"]
    assert i18n.plural_category("en-GB", 1) == "one"


def test_plurals_use_the_catalogs_forms(locales):
    locales({"en-GB": {"_language": "English (UK)",
                       "{count} meter|{count} meters": {"one": "{count} metre", "other": "{count} metres"}}})
    assert tn("{count} meter", "{count} meters", 1, lang="en-GB") == "1 metre"
    assert tn("{count} meter", "{count} meters", 3, lang="en-GB") == "3 metres"


def test_context_separates_one_source(locales):
    locales({"fr": {"_language": "Français", "Open": "Ouvert", "file\u0004Open": "Ouvrir"}})
    assert t("Open", lang="fr") == "Ouvert"
    assert t("Open", lang="fr", context="file") == "Ouvrir"
    assert t("Open", context="file") == "Open"


def test_unknown_plural_rules_use_other_with_one_warning(locales, caplog):
    locales({"xx": {"_language": "Test", "{count} part|{count} parts": {"other": "{count} xparts"}}})
    with caplog.at_level(logging.WARNING, logger="scrimmage.i18n"):
        assert i18n.plural_category("xx", 1) == "other"
        assert tn("{count} part", "{count} parts", 1, lang="xx") == "1 xparts"
        assert tn("{count} part", "{count} parts", 2, lang="xx") == "2 xparts"
    assert len(caplog.records) == 1 and "no plural rules for xx" in caplog.records[0].getMessage()


# -- a broken entry falls back, with one warning (L6, Review Focus 2) -------------------------------------------------
@pytest.mark.parametrize("key,entry,why", [
    ("Saved {name}", "Enregistré {nom}", "{nom}"),
    ("Saved {name}", 3, "should be text"),
    ("{count} part|{count} parts", "{count} parties", "one and other"),
    ("{count} part|{count} parts", {"one": "{count} partie"}, "other"),
    ("{count} part|{count} parts", {"one": "{n} partie", "other": "{count} parties"}, "{n}"),
])
def test_a_broken_entry_falls_back_with_one_warning(locales, caplog, key, entry, why):
    locales({"en-AU": {"_language": "English (Australia)", key: entry}})
    with caplog.at_level(logging.WARNING, logger="scrimmage.i18n"):
        for _ in range(3):
            if "|" in key:
                assert tn("{count} part", "{count} parts", 1, lang="en-AU") == "1 part"
                assert tn("{count} part", "{count} parts", 2, lang="en-AU") == "2 parts"
            else:
                assert t(key, lang="en-AU", name="x") == "Saved x"
    warnings = [r.getMessage() for r in caplog.records]
    assert len(warnings) == 1, warnings
    assert why in warnings[0] and "en-AU.json" in warnings[0] and "English" in warnings[0]


def test_a_broken_catalog_is_english_with_one_warning(locales, caplog):
    with caplog.at_level(logging.WARNING, logger="scrimmage.i18n"):
        locales({"fr": "{not json"})
        assert i18n.languages() == {"en-US": "English (US)", "fr": "fr"}       # still offered, named by its tag
        for _ in range(3):
            assert t("Color", lang="fr") == "Color"
    warnings = [r.getMessage() for r in caplog.records]
    assert len(warnings) == 1 and "fr.json" in warnings[0] and "isn't valid JSON" in warnings[0], warnings


def test_an_empty_translation_is_untranslated_without_a_warning(locales, caplog):
    locales({"fr": {"_language": "Français", "Color": "", "{count} part|{count} parts": {"one": "", "other": ""}}})
    with caplog.at_level(logging.WARNING, logger="scrimmage.i18n"):
        assert t("Color", lang="fr") == "Color"
        assert tn("{count} part", "{count} parts", 2, lang="fr") == "2 parts"
    assert not caplog.records


# -- the languages, and the Language setting (L5) ---------------------------------------------------------------------
def test_languages_are_the_catalogs_in_the_folder(locales):
    folder = locales({"fr": {"_language": "Français"}, "de-CH": {"_language": "Deutsch (Schweiz)"}})
    (folder / "fr.spellings.json").write_text("{}")              # a helper file, not a language
    (folder / "notes.txt").write_text("")
    (folder / "Not A Tag.json").write_text("{}")
    i18n.reload()
    assert i18n.languages() == {"en-US": "English (US)", "de-CH": "Deutsch (Schweiz)", "fr": "Français"}


def test_the_setting_picks_the_language(language):
    assert settings.load().language == "en-US"
    language("en-GB")
    assert settings.load().language == "en-GB" and i18n.current_language() == "en-GB"
    assert t(CG) == CG_GB
    assert (settings.settings_dir() / "settings.toml").read_text() == 'language = "en-GB"\n'
    settings.save(language="en-us")                            # a tag's case doesn't matter
    assert settings.load().language == "en-US"


def test_language_and_units_are_separate(language, units):
    units("metric")
    language("en-GB")
    assert settings.load() == settings.Settings(units="metric", language="en-GB")
    units("imperial")
    assert settings.load().language == "en-GB"


def test_save_refuses_a_language_scrimmage_doesnt_have():
    with pytest.raises(ValueError, match='language must be one of "en-US", "en-GB", not "fr"'):
        settings.save(language="fr")
    assert not (settings.settings_dir() / "settings.toml").exists()


@pytest.mark.parametrize("text,why", [
    ('language = "fr"\n', 'language = "fr" isn\'t one of Scrimmage\'s languages (en-US, en-GB)'),
    ("language = 7\n", "language = 7 isn't one of Scrimmage's languages"),
])
def test_a_bad_language_falls_back_to_american_english_with_one_warning(caplog, text, why):
    f = settings.settings_dir() / "settings.toml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('units = "metric"\n' + text)
    with caplog.at_level(logging.WARNING, logger="scrimmage.settings"):
        for _ in range(3):
            assert settings.load() == settings.Settings(units="metric", language="en-US")   # units still apply
            assert i18n.current_language() == "en-US"
    warnings = [r.getMessage() for r in caplog.records]
    assert len(warnings) == 1, warnings
    assert why in warnings[0] and "using American English (en-US)" in warnings[0]
    assert settings.problem() == warnings[0]


def test_a_bad_units_and_language_say_both():
    f = settings.settings_dir() / "settings.toml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('units = "furlongs"\nlanguage = "fr"\n')
    assert settings.load() == settings.Settings()
    assert "using imperial units" in settings.problem() and "using American English" in settings.problem()


def test_the_language_override_wins_over_the_file(language):
    language("en-GB")
    settings.set_language_override("en-US")
    assert i18n.current_language() == "en-US" and settings.load().language == "en-GB"
    settings.set_language_override(None)
    assert i18n.current_language() == "en-GB"
    with pytest.raises(ValueError):
        settings.set_language_override("tlh")


def test_the_language_fixture_sets_the_language(language):
    assert i18n.current_language() == "en-US"
    language("en-GB")
    assert i18n.current_language() == "en-GB"


# -- scrimmage --language ------------------------------------------------------------------------------------------
def test_cli_language_overrides_the_file(monkeypatch, language):
    from scrimmage import __main__ as cli
    language("en-GB")
    seen = []
    # the flag sets the Language (settings.current_language(), what `edit` and `serve` follow), while a command other
    # than those writes American English (i18n.current_language(), inside the CLI's one guard)
    monkeypatch.setattr(cli, "_java", lambda args: seen.append((settings.current_language(), settings.current_units()))
                        or written.append(i18n.current_language()))
    written = []
    cli.main(["--language", "en-US", "java", "path"])
    cli.main(["--language=en-gb", "--units", "metric", "java", "path"])
    settings.set_language_override(None)
    settings.set_override(None)
    cli.main(["--units", "metric", "--language", "en-US", "java", "path"])     # either order
    settings.set_language_override(None)
    cli.main(["java", "path"])                     # without it, the settings file
    assert seen == [("en-US", "imperial"), ("en-GB", "metric"), ("en-US", "metric"), ("en-GB", "metric")]
    assert written == ["en-US"] * 4


def test_cli_language_before_measure(monkeypatch):
    from scrimmage import __main__ as cli
    from scrimmage.measure import cli as measure_cli
    seen = []
    monkeypatch.setattr(measure_cli, "main", lambda argv: seen.append((argv, settings.current_language(),
                                                                       i18n.current_language())) or 0)
    with pytest.raises(SystemExit):
        cli.main(["--language", "en-GB", "measure", "form", "ball"])
    settings.set_language_override(None)
    assert seen == [(["form", "ball"], "en-GB", "en-US")]       # the setting reached it; it prints American English


def test_cli_language_refuses_another_value(capsys):
    from scrimmage import __main__ as cli
    with pytest.raises(SystemExit) as e:
        cli.main(["--language", "fr", "java", "path"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "--language" in err and "en-GB" in err and settings._language_override is None


def test_cli_help_names_the_languages():
    out = subprocess.run([sys.executable, "-m", "scrimmage", "--help"], capture_output=True, text=True,
                         check=True).stdout
    assert "--language {en-US,en-GB}" in out


# -- machine paths stay en-US (L2, Review Focus 5) --------------------------------------------------------------------
def test_the_machine_guards_fix_the_language(language):
    language("en-GB")
    assert t(CG) == CG_GB
    with units.machine_text():
        assert i18n.current_language() == "en-US"
        assert t(CG) == CG
        assert tn(*PIECES, 2, body="arm") == "2 pieces in arm are still guessed as aluminum"
        assert t(CG, lang="en-GB") == CG_GB         # an explicit lang= still wins
    with units.fixed("metric"):
        assert t(CG) == CG and units.current_system() == "metric"
    with i18n.fixed("en-GB"):
        assert t(CG) == CG_GB
        with units.machine_text():
            assert t(CG) == CG
        assert t(CG) == CG_GB
    assert t(CG) == CG_GB


def test_the_machine_guard_is_per_thread(language):
    import threading
    language("en-GB")
    seen = []
    with units.machine_text():
        th = threading.Thread(target=lambda: seen.append(t(CG)))
        th.start()
        th.join()
        seen.append(t(CG))
    assert seen == [CG_GB, CG]


# -- catalog_json, for the page --------------------------------------------------------------------------------------
def test_catalog_json_is_the_differing_entries():
    assert i18n.catalog_json("en-US") == {}
    gb = i18n.catalog_json("en-GB")
    assert gb[CG] == CG_GB and "_language" not in gb
    assert gb["|".join(PIECES)] == {"one": PIECES[0].replace("aluminum", "aluminium"),
                                  "other": PIECES[1].replace("aluminum", "aluminium")}


def test_the_spellings_file():
    pairs = json.loads((ROOT / "scrimmage" / "locales" / "en-GB.spellings.json").read_text(encoding="utf-8"))
    for us, gb in (("color", "colour"), ("center", "centre"), ("meter", "metre"), ("meters", "metres"),
                   ("millimeter", "millimetre"), ("aluminum", "aluminium"), ("gray", "grey"), ("fiber", "fibre"),
                   ("modeled", "modelled"), ("modeling", "modelling"), ("neighboring", "neighbouring"),
                   ("behavior", "behaviour"), ("favorite", "favourite"), ("analyze", "analyse"),
                   ("organize", "organise"), ("liter", "litre"), ("canceled", "cancelled"),
                   ("traveled", "travelled"), ("labeled", "labelled"), ("defense", "defence"),
                   ("catalog", "catalogue")):
        assert pairs[us] == gb, us
    assert all(k == k.lower() and v == v.lower() and k != v for k, v in pairs.items() if not k.startswith("_"))


def test_the_locales_ship_in_the_package():
    import tomllib
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "locales/*.json" in data["tool"]["setuptools"]["package-data"]["scrimmage"]


# -- the tool: scrimmage i18n template | check ------------------------------------------------------------------------
PY_SOURCE = '''\
from scrimmage.i18n import t, tn as plural
from scrimmage import i18n

def f(name, n, what):
    a = t("Saved {name}", name=name)
    b = plural("{count} part", "{count} parts", n)
    c = i18n.t("Open", context="file")
    d = t("Unused in " "two pieces")
    e = t(what)
    g = t(f"Hi {name}")
    h = t("Color")
    return t("Saved {name}", name="again")
'''

JS_SOURCE = '''\
import { t, tn } from "./i18n.js";
import { i18n } from "./i18n.js";
// t("in a comment") is not a call
const s = "t('in a string') neither";
const re = /t\\(x/g;
export function show(n, who) {
  const a = t('It\\'s {who}\\u0021', { who });
  const b = tn("{count} ball", "{count} balls", n);
  const c = `${t("Inside a template")} and ${n}`;
  const d = t("Joined " + who);
  const e = t(`Hello ${who}`);
  const f = i18n.t("Open", { context: "menu" });
  const g = t(
    "Across lines");
  return [a, b, c, d, e, f, g, t("Color")];
}
'''


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "pkg"
    (root / "static" / "vendor").mkdir(parents=True)
    (root / "mod.py").write_text(PY_SOURCE)
    (root / "other.py").write_text("def t(x):\n    return x\n\nt(1)\n")    # not i18n's t: not a call it scans
    (root / "static" / "page.js").write_text(JS_SOURCE)
    (root / "static" / "vendor" / "lib.js").write_text('import { t } from "./i18n.js"; t(x);\n')    # not scanned
    return root


def test_template_finds_the_literal_sources(tree):
    found, errors = i18n_tool.scan(tree)
    where = {s.key: [w for w in s.where] for s in found}
    assert where == {
        "Saved {name}": ["pkg/mod.py:5", "pkg/mod.py:12"],
        "{count} part|{count} parts": ["pkg/mod.py:6"],
        "file\u0004Open": ["pkg/mod.py:7"],
        "Unused in two pieces": ["pkg/mod.py:8"],
        "Color": ["pkg/mod.py:11", "pkg/static/page.js:15"],
        "It's {who}!": ["pkg/static/page.js:7"],
        "{count} ball|{count} balls": ["pkg/static/page.js:8"],
        "Inside a template": ["pkg/static/page.js:9"],
        "menu\u0004Open": ["pkg/static/page.js:12"],
        "Across lines": ["pkg/static/page.js:13"],
    }
    assert [s.plural for s in found if s.plural] == [True, True]
    assert errors == [
        "pkg/mod.py:9: t() needs a literal string first, not what",
        "pkg/mod.py:10: t() needs a literal string first, not an f-string",
        "pkg/static/page.js:10: t() needs a literal string first, not one joined with +",
        "pkg/static/page.js:11: t() needs a literal string first, not a template with ${...}",
    ]


def test_a_python_file_that_doesnt_parse_is_an_error_not_a_crash(tree):
    (tree / "broken.py").write_text("from scrimmage.i18n import t\n\ndef f(:\n    t('Never seen')\n")
    found, errors = i18n_tool.scan(tree)
    assert "Color" in {s.key for s in found} and "Never seen" not in {s.key for s in found}   # the rest still scanned
    assert [e for e in errors if e.startswith("pkg/broken.py")] == ["pkg/broken.py:3: isn't valid Python (invalid syntax)"]


def test_template_cli_lists_and_writes_a_starter(tree, tmp_path):
    out = tmp_path / "xx.json"
    run = subprocess.run([sys.executable, "-m", "scrimmage", "i18n", "template", "--root", str(tree),
                          "--out", str(out)], capture_output=True, text=True)
    assert run.returncode == 1                                   # the non-literal calls
    assert 'pkg/mod.py:5: "Saved {name}"' in run.stdout
    assert 'pkg/static/page.js:12: "menu\\u0004Open"' in run.stdout
    assert "pkg/mod.py:9: t() needs a literal string first, not what" in run.stderr
    starter = json.loads(out.read_text(encoding="utf-8"))
    assert starter["_language"] == "" and starter["Color"] == ""
    assert starter["{count} ball|{count} balls"] == {"one": "", "other": ""}
    assert len(starter) == 11


def test_check_reports_missing_unused_and_mismatched(tree, tmp_path):
    loc = tmp_path / "loc"
    loc.mkdir()
    (loc / "fr.json").write_text(json.dumps({
        "_language": "Français",
        "Color": "Couleur",
        "Saved {name}": "Enregistré",                               # drops {name}: a mismatch
        "{count} part|{count} parts": {"one": "{n} partie", "other": "{count} parties"},   # broken
        "Gone": "Parti",                                            # unused
    }))
    (loc / "de.json").write_text(json.dumps({"_language": "Deutsch", "Color": "Farbe"}))
    found, _ = i18n_tool.scan(tree)
    report = i18n_tool.check("fr", found, loc)
    assert report.unused == ["Gone"]
    assert report.mismatched == ["Saved {name}: the translation drops {name}"]
    assert report.broken == ["{count} part|{count} parts: one uses {n}, which the source doesn't have"]
    assert "Open" not in report.missing and "file\u0004Open" in report.missing and len(report.missing) == 7

    def cli(*args):
        return subprocess.run([sys.executable, "-m", "scrimmage", "i18n", "check", *args, "--root", str(tree),
                               "--locales", str(loc)], capture_output=True, text=True)
    run = cli("fr")
    assert run.returncode == 1, run.stdout + run.stderr
    assert "fr (Français)" in run.stdout and "Gone" in run.stdout and "drops {name}" in run.stdout
    (tree / "mod.py").write_text(PY_SOURCE.replace("    e = t(what)\n", "").replace('    g = t(f"Hi {name}")\n', ""))
    (tree / "static" / "page.js").write_text(JS_SOURCE.replace('  const d = t("Joined " + who);\n', "")
                                             .replace("  const e = t(`Hello ${who}`);\n", ""))
    run = cli("de")
    assert run.returncode == 0, run.stdout + run.stderr        # missing only
    assert "missing" in run.stdout and "Inside a template" in run.stdout
    run = cli()                                                # every catalog: fr's mismatches fail it
    assert run.returncode == 1 and "de (Deutsch)" in run.stdout and "fr (Français)" in run.stdout
    run = cli("tlh")
    assert run.returncode == 2 and "tlh" in run.stderr


def test_the_real_tree_has_no_non_literal_calls():
    found, errors = i18n_tool.scan(ROOT / "scrimmage")
    assert errors == []
    assert any(s.key == "Couldn't change the setting: {why}" for s in found)
