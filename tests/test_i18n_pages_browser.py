"""The Robot (Assembler and its Robot code panel), Part, Drive and Start pages in another language, in headless
Chromium: index.html's data-i18n text, each tab's t() text and the server's words follow the Language setting live,
and a half-typed box survives the switch.

Some tests use the shipped en-GB catalog (its British spellings); the others swap in a catalog of their own through
the settings answer (catalog()), so they can check words en-GB spells the same. The fixtures live in
tests/test_assembler_browser.py and tests/test_part_browser.py.
"""
import json

import pytest

from test_assembler_browser import Server, browser, make_page  # noqa: F401  (fixtures)
from test_part_browser import _open
from browser_harness import close_context

pytestmark = pytest.mark.browser


def catalog(page, entries):
    """Answer the page's settings requests as en-GB with these entries (and their own catalog_version), and load."""
    def answer(route):
        res = route.fetch()
        data = res.json()
        data.update(language="en-GB", catalog=entries, catalog_version="test-" + str(len(entries)))
        route.fulfill(response=res, body=json.dumps(data))
    page.route("**/api/settings*", answer)
    page.evaluate("import('./settings.js').then((m) => m.load())")
    page.wait_for_function("document.documentElement.lang === 'en-GB'")


def english(page):
    page.unroute("**/api/settings*")
    page.evaluate("import('./settings.js').then((m) => m.load())")
    page.wait_for_function("document.documentElement.lang === 'en-US'")


def switch(page, tag):
    assert page.evaluate("(tag) => import('./i18n.js').then((m) => m.setLanguage(tag))", tag) is None
    page.wait_for_function("(tag) => document.documentElement.lang === tag", arg=tag)


# -- the Robot tab -------------------------------------------------------------------------------------------------
def test_the_robot_tab_in_en_gb(make_page, language, units):
    language("en-GB")
    units("metric")
    page, _ = make_page("kitbot.toml")
    assert page.get_attribute("html", "lang") == "en-GB"
    # a translated string, and a sentence whose placeholder is filled in the new words
    assert page.get_attribute("#cg", "title") == \
        "The robot's centre of gravity at its start pose, from the floor at the frame's centre"
    assert page.get_attribute("#insp-pos-0", "aria-label") == "Offset x (millimetres)"
    # a stock part's parameter docs are the server's (parts/stock, N_()), in the same language
    assert "distance from each frame edge to a corner mount (module centre)" in page.text_content("#inspector")


def test_a_half_typed_inspector_box_survives_a_language_switch(make_page, units):
    """Review Focus 3: the inspector redraws in the new words, and the box being typed in keeps its text and focus."""
    units("metric")
    page, cfg = make_page("kitbot.toml")
    before = cfg.read_text()
    page.click("#insp-p-length")
    page.fill("#insp-p-length", "700")
    switch(page, "en-GB")
    page.wait_for_function("document.querySelector('#insp-pos-0').getAttribute('aria-label') === 'Offset x (millimetres)'")
    assert page.input_value("#insp-p-length") == "700"
    assert page.evaluate("document.activeElement.id") == "insp-p-length"
    assert cfg.read_text() == before          # nothing was committed by the switch
    switch(page, "en-US")
    assert page.get_attribute("#insp-pos-0", "aria-label") == "Offset x (millimeters)"
    assert page.input_value("#insp-p-length") == "700"


def test_the_pages_markup_follows_the_language(make_page):
    page, _ = make_page("kitbot.toml")
    shown = page.text_content("#file-path")          # a script wrote it: data-i18n leaves it alone
    catalog(page, {"Parts library": "The parts", "Undo (Ctrl+Z)": "Take it back (Ctrl+Z)",
                   "The file this tab edits": "Not this", "verb\u0004Step": "One step", "Settings": "Choices"})
    assert page.text_content("#library-title") == "The parts"
    assert page.get_attribute("#undo", "title") == "Take it back (Ctrl+Z)"
    assert page.text_content("#drive-step") == "One step"          # data-i18n-context="verb"
    assert page.get_attribute("#settings-menu", "aria-label") == "Choices"
    assert page.text_content("#file-path") == shown
    english(page)
    assert page.text_content("#library-title") == "Parts library"
    assert page.get_attribute("#undo", "title") == "Undo (Ctrl+Z)"


def test_the_robot_code_panel_follows_the_language(make_page):
    page, _ = make_page("kitbot.toml")
    page.click("#center-code")
    page.wait_for_function("(document.querySelector('#code-battery-voltage') || {}).placeholder === '12 (the default)'")
    catalog(page, {"{value} (the default)": "default: {value}", "More settings": "Further settings",
                   "Battery voltage": "Battery volts"})
    page.wait_for_function("document.querySelector('#code-battery-voltage').placeholder === 'default: 12'")
    assert page.text_content("#code-more summary") == "Further settings"
    assert page.text_content("label[for='code-battery-voltage']") == "Battery volts"


# -- the Drive tab -------------------------------------------------------------------------------------------------
def test_the_drive_tab_follows_the_language(make_page):
    page, _ = make_page("kitbot.toml")
    page.click("#tab-btn-drive")
    page.wait_for_function("document.querySelector('#drive-state').textContent === 'Not started'")
    catalog(page, {"Not started": "Not begun", "Port {n}": "Joystick port {n}", "Joysticks": "Sticks"})
    page.wait_for_function("document.querySelector('#drive-state').textContent === 'Not begun'")
    assert page.text_content("[data-port='0'] .port-label") == "Joystick port 0"
    assert "Sticks" in page.evaluate("[...document.querySelectorAll('#drive-ds h3')].map((e) => e.textContent)")


# -- the Part tab --------------------------------------------------------------------------------------------------
def test_the_part_tab_in_en_gb(browser, tmp_path, monkeypatch, language):
    language("en-GB")
    page, _, close = _open(browser, tmp_path, monkeypatch, "climber.step")
    try:
        line = page.text_content("#part-inspector .part-line")
        assert line.startswith("Weight ") and " · centre of gravity x " in line
        assert "centre of gravity" in page.text_content(".part-legend")
        page.click('#part-tree [data-body="arm_A"]')
        page.wait_for_selector("#part-body-mass")
        assert "Centre of gravity" in page.text_content("#part-inspector")
    finally:
        close()


# -- the Start page ------------------------------------------------------------------------------------------------
def test_the_start_page_follows_the_language_and_keeps_a_typed_name(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    srv = Server(None, cwd=tmp_path)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    try:
        page = ctx.new_page()
        page.goto(srv.url)
        page.wait_for_selector("#start-page h1")
        page.click("[data-key='new-robot']")
        page.fill("#start-name", "mybot")
        catalog(page, {"Recent": "Lately", "Make something new": "Make a new thing", "Create": "Make it",
                       "A file name such as mybot (.toml is added).": "A name such as mybot (.toml goes on the end)."})
        page.wait_for_function("[...document.querySelectorAll('#start-page h2')].some((e) => e.textContent === 'Lately')")
        assert page.text_content("#start-create") == "Make it"
        assert "A name such as mybot (.toml goes on the end)." in page.text_content("#start-new")
        assert page.input_value("#start-name") == "mybot"           # the form wasn't rebuilt
    finally:
        close_context(ctx)
        srv.stop()
