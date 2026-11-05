"""The Robot, Part, Drive and Start pages' words on the server side: index.html's data-i18n sources (and the tool
reading them), N_() data sources (stock part docs), the settings poll that sends the catalog only when it changes,
the server's own words in en-GB, and the codes the page acts on instead of the words (L3)."""
from __future__ import annotations

import html
from html.parser import HTMLParser
from pathlib import Path

import pytest

from scrimmage import i18n, i18n_tool
from scrimmage.assembler import library, templates
from scrimmage.assembler.server import App
from test_assembler_server import Client

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "scrimmage" / "assembler" / "static" / "index.html"


@pytest.fixture
def start(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.chdir(tmp_path)
    started = []

    def make(path=None):
        app = App(path, open_browser=False, runs_dir=tmp_path / "runs")
        started.append(app)
        return Client(app, app.start())
    yield make
    for a in started:
        a.stop()


# -- index.html ----------------------------------------------------------------------------------------------------
class _Marked(HTMLParser):
    """Each element with data-i18n*: (line, tag, attrs, its text, whether it holds other elements)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.found = [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("input", "meta", "link", "br", "img"):
            a = dict(attrs)
            if any(k.startswith("data-i18n") for k in a):
                self.found.append([self.getpos()[0], tag, a, "", False])
            return
        for item in self.stack:
            item[4] = True
        a = dict(attrs)
        item = [self.getpos()[0], tag, a, "", False]
        self.stack.append(item)
        if any(k.startswith("data-i18n") for k in a):
            self.found.append(item)

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[1] == tag:
                break

    def handle_data(self, data):
        for item in self.stack:
            item[3] += data


def test_each_data_i18n_source_is_the_markup_it_replaces():
    """i18n.js writes t(source) over the element's text (or attribute): so the text must be that source, in an
    element holding only text, or en-US would read differently (and a child element would be lost)."""
    p = _Marked()
    p.feed(INDEX.read_text(encoding="utf-8"))
    norm = lambda s: " ".join(html.unescape(s).split())
    assert len(p.found) > 150
    for line, tag, a, text, nested in p.found:
        if "data-i18n" in a:
            assert not nested, f"index.html:{line}: <{tag} data-i18n> holds other elements"
            assert norm(text) == a["data-i18n"], f"index.html:{line}"
        for attr in ("title", "placeholder", "aria-label"):
            if f"data-i18n-{attr}" in a:
                assert a.get(attr) == a[f"data-i18n-{attr}"], f"index.html:{line}: {attr}"
        assert any(k in a for k in ("data-i18n", "data-i18n-title", "data-i18n-placeholder", "data-i18n-aria-label")), \
            f"index.html:{line}: data-i18n-context alone"


def test_the_tool_reads_the_pages_markup(tmp_path):
    root = tmp_path / "pkg"
    (root / "static").mkdir(parents=True)
    (root / "static" / "page.html").write_text(
        '<!doctype html>\n<title data-i18n="Page">Page</title>\n'
        '<button title="Undo it" data-i18n-title="Undo it" data-i18n="Undo">Undo</button>\n'
        '<button data-i18n-context="verb" data-i18n="Step">Step</button>\n'
        '<input placeholder="e.g. R" data-i18n-placeholder="e.g. R" data-i18n-aria-label="Game data">\n'
        '<p data-i18n="">Empty</p>\n', encoding="utf-8")
    found, errors = i18n_tool.scan(root)
    assert {s.key: s.where for s in found} == {
        "Page": ["pkg/static/page.html:2"], "Undo it": ["pkg/static/page.html:3"], "Undo": ["pkg/static/page.html:3"],
        "verb\u0004Step": ["pkg/static/page.html:4"], "e.g. R": ["pkg/static/page.html:5"],
        "Game data": ["pkg/static/page.html:5"]}
    assert errors == ["pkg/static/page.html:6: t() needs a literal string first, not an empty attribute"]


def test_the_real_index_html_is_in_the_template():
    found, errors = i18n_tool.scan()
    assert not errors
    where = {s.key: s.where for s in found}
    assert any(w.endswith("static/index.html:181") for w in where["Parts library"])
    assert "verb\u0004Step" in where


# -- N_(): data that is translated where it is shown -----------------------------------------------------------------
def test_n_marks_a_data_source_and_translated_shows_it(tmp_path, language):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "data.py").write_text('from .i18n import N_\nDOCS = [N_("wheel center"), N_(name)]\n')
    found, errors = i18n_tool.scan(root)
    assert [s.key for s in found] == ["wheel center"] and not found[0].plural
    assert errors == ["pkg/data.py:2: t() needs a literal string first, not name"]
    assert i18n.N_("frame width") == "frame width"
    language("en-GB")
    assert i18n.translated("distance from each frame edge to a corner mount (module center)") == \
        "distance from each frame edge to a corner mount (module centre)"
    assert i18n.translated("not in any catalog") == "not in any catalog"
    assert i18n.translated("") == ""


def test_stock_docs_follow_the_language(language):
    doc = "distance from each frame edge to a corner mount (module center)"

    def chassis_doc():
        chassis = next(s for s in library.stock() if s["name"] == "chassis")
        return next(p["doc"] for p in chassis["params"] if p["doc"].startswith("distance from each frame edge"))
    assert chassis_doc() == doc
    language("en-GB")
    assert chassis_doc() == doc.replace("center", "centre")


# -- the settings poll ---------------------------------------------------------------------------------------------
def test_the_settings_poll_leaves_out_a_catalog_the_page_has(start, language):
    c = start()
    language("en-GB")
    s, first = c.call("GET", "/api/settings")
    assert s == 200 and first["catalog"]["Color"] == "Colour"
    version = first["catalog_version"]
    assert version == i18n.catalog_version("en-GB") != i18n.catalog_version("en-US")
    s, again = c.call("GET", f"/api/settings?catalog={version}")
    assert s == 200 and "catalog" not in again and again["catalog_version"] == version
    assert {k: v for k, v in again.items()} == {k: v for k, v in first.items() if k != "catalog"}
    s, other = c.call("GET", "/api/settings?catalog=stale")
    assert other["catalog"] == first["catalog"]
    # the language changed since: the catalog comes again, the new language's
    s, now = c.call("POST", "/api/settings", {"language": "en-US"})
    assert now["catalog"] == {} and now["catalog_version"] != version
    s, back = c.call("GET", f"/api/settings?catalog={version}")
    assert back["language"] == "en-US" and back["catalog"] == {}


# -- the server's words --------------------------------------------------------------------------------------------
def _robot(tmp_path) -> Path:
    p = tmp_path / "bot.toml"
    p.write_text(templates.new_config("swerve"))
    return p


def test_a_server_validation_message_in_en_gb(start, tmp_path, language):
    c = start(_robot(tmp_path))
    bad = {"op": "start", "pose": [1.0, 2.0], "range": None}
    s, b = c.post("/api/robot/edit", bad)
    assert b["errors"] == [{"field": "start/pose",
                            "message": "the start must be [x, y, heading]: meters from the blue corner, and degrees"}]
    language("en-GB")
    s, b = c.post("/api/robot/edit", bad)
    assert b["errors"] == [{"field": "start/pose",
                            "message": "the start must be [x, y, heading]: metres from the blue corner, and degrees"}]


def test_refusals_the_page_acts_on_carry_codes(start, tmp_path, monkeypatch):
    """L3: the robot code panel told "already live" and "a scenario run" apart by their words; now by field."""
    from scrimmage.assembler import live as live_mod
    from scrimmage.assembler.simlock import SimBusy

    c = start(_robot(tmp_path))
    c.post("/api/robot/edit", {"op": "start", "pose": [2.0, 2.0, 0.0], "range": None})
    app = c.app
    monkeypatch.setattr(app, "_not_built", lambda: None)
    monkeypatch.setattr(app, "_drivable", lambda: (tmp_path / "bot.toml", type("C", (), {"step": 0.02})(), None))

    def busy(*a, **k):
        raise SimBusy("a scenario run is in progress", "run")
    monkeypatch.setattr(app.live, "start", busy)
    s, b = c.call("POST", "/api/robot/check", {})
    assert s == 409 and b["errors"][0]["field"] == "busy/run"

    def live(*a, **k):
        raise live_mod.AlreadyLive("a drive session is already live")
    monkeypatch.setattr(app.live, "start", live)
    s, b = c.call("POST", "/api/robot/check", {})
    assert s == 409 and b["errors"][0] == {"field": "live/already", "message": "a drive session is already live"}


def test_a_recording_that_exists_says_so_by_its_type():
    """L3: Save as scenario's "already exists" (the page asks Overwrite?) was told by its last words."""
    from scrimmage.assembler.live import LiveError, RecordingTaken
    from scrimmage.assembler.recorder import RecordingError, RecordingExists
    assert issubclass(RecordingExists, RecordingError) and issubclass(RecordingTaken, LiveError)


def test_the_code_panels_problem_comes_as_a_finished_sentence(start, tmp_path):
    """The page shows problem_text as it is (no capitalizing or full stop added in JS, so a translation's own
    punctuation stays); problem keeps its old words for the API."""
    cfg = _robot(tmp_path)
    cfg.write_text(cfg.read_text() + '\n[robot_program]\ncommand = ["./gradlew"]\ncwd = "moved"\n')
    c = start(cfg)
    b = c.get("/api/robot/code")[1]
    assert b["problem"].startswith("the robot code folder moved isn't there")
    assert b["problem_text"] == "The robot code folder moved isn't there (was it moved?): pick it again."
    (tmp_path / "moved").mkdir()
    b = c.get("/api/robot/code")[1]
    assert b["problem_text"] == "Nothing here scrimmage can start: no build.gradle or robot.py."
