"""The page's i18n.js against scrimmage/i18n.py, and the Settings menu's Language row, in headless Chromium.

The fixtures (Chromium lookup, the Server helper) live in tests/test_assembler_browser.py.
"""
import pytest

from scrimmage import settings
from scrimmage.i18n import t, tn
from test_assembler_browser import Server, browser  # noqa: F401  (fixtures)

pytestmark = pytest.mark.browser


@pytest.fixture
def page(browser, tmp_path):
    srv = Server(None, cwd=tmp_path)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    logs, warnings = [], []
    p = ctx.new_page()
    p.set_default_timeout(20000)
    p.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
    p.on("console", lambda m: logs.append(f"console.{m.type}: {m.text}") if m.type == "error" else
         warnings.append(m.text) if m.type == "warning" else None)
    p.goto(srv.url)
    p.wait_for_function("window.__scrimmage && __scrimmage.mode")
    p.evaluate("async () => { window.I = await import('./i18n.js'); }")
    p.console_warnings = warnings
    yield p
    ctx.close()
    srv.stop()
    assert not logs, f"browser errors: {logs}"


def _switch(page, tag):
    assert page.evaluate("(l) => I.setLanguage(l)", tag) is None
    assert page.evaluate("I.i18n.lang") == tag


CASES = [("Color", {}), ("Saved {name}", {"name": "intake"}), ("A literal {{brace}} and {x}", {"x": 3}),
         ("Center of gravity", {})]
PLURALS = [("{count} meter", "{count} meters", n) for n in (0, 1, 2, 1.5)] + \
          [("{count} part", "{count} parts", n) for n in (1, 3)]


def test_js_translates_as_python_does(page):
    assert page.evaluate("I.i18n.languages") == {"en-US": "English (US)", "en-GB": "English (UK)"}
    for tag in ("en-US", "en-GB", "en-US"):
        _switch(page, tag)
        got = page.evaluate("(cases) => cases.map(([s, p]) => I.t(s, p))", CASES)
        assert got == [t(s, lang=tag, **p) for s, p in CASES], tag
        got = page.evaluate("(cases) => cases.map(([a, b, n]) => I.tn(a, b, n))", PLURALS)
        assert got == [tn(a, b, n, lang=tag) for a, b, n in PLURALS], tag
    assert "missing a value for {name}" in page.evaluate(
        "(() => { try { I.t('Saved {name}'); } catch (e) { return e.message; } })()")


def test_a_broken_entry_falls_back_with_one_console_warning(page):
    import json

    def broken(route):
        res = route.fetch()
        data = res.json()
        data.update(language="en-GB", catalog={"Saved {name}": "Saved {nom}",
                                               "{count} part|{count} parts": "not an object", "Color": "Colour"})
        route.fulfill(response=res, body=json.dumps(data))
    page.route("**/api/settings", broken)
    page.evaluate("import('./settings.js').then((m) => m.load())")
    assert page.evaluate("I.i18n.lang") == "en-GB"
    for _ in range(3):
        assert page.evaluate("I.t('Saved {name}', { name: 'x' })") == "Saved x"
        assert page.evaluate("I.tn('{count} part', '{count} parts', 2)") == "2 parts"
        assert page.evaluate("I.t('Color')") == "Colour"
    broken_ = [w for w in page.console_warnings if "en-GB" in w]
    assert len(broken_) == 2, page.console_warnings
    assert any("{nom}" in w for w in broken_) and any("one and other" in w for w in broken_)


def test_the_language_row_switches_and_the_page_rerenders(page):
    page.evaluate("""() => {
      window.notReloaded = true;
      const el = document.createElement('output');
      el.id = 'demo';
      const show = () => { el.textContent = I.t('Color') + ' ' + I.tn('{count} meter', '{count} meters', 2); };
      I.onChange(show); show();
      document.body.append(el);
    }""")
    assert page.text_content("#demo") == "Color 2 meters"
    assert page.get_attribute("html", "lang") == "en-US"
    page.click("#settings-btn")
    assert page.is_visible("[data-setting='language'][data-value='en-GB']")
    assert page.text_content("[data-setting='language'][data-value='en-GB']") == "English (UK)"
    assert page.get_attribute("[data-setting='language'][data-value='en-US']", "aria-pressed") == "true"
    page.click("[data-setting='language'][data-value='en-GB']")
    page.wait_for_function("document.getElementById('demo').textContent === 'Colour 2 metres'")
    assert page.get_attribute("html", "lang") == "en-GB"
    assert page.get_attribute("[data-setting='language'][data-value='en-GB']", "aria-pressed") == "true"
    assert page.get_attribute("[data-setting='language'][data-value='en-US']", "aria-pressed") == "false"
    assert page.get_attribute("[data-setting='units'][data-value='imperial']", "aria-pressed") == "true"
    assert settings.load().language == "en-GB" and settings.load().units == "imperial"
    page.keyboard.press("Escape")
    # a change made elsewhere (another editor, the CLI, by hand) arrives by the poll
    (settings.settings_dir() / "settings.toml").write_text('language = "en-US"\n')
    page.wait_for_function("document.getElementById('demo').textContent === 'Color 2 meters'", timeout=10000)
    assert page.get_attribute("html", "lang") == "en-US"
    assert page.evaluate("window.notReloaded") is True
    # Units still switches on its own, and doesn't change the language
    page.click("#settings-btn")
    page.click("[data-setting='units'][data-value='metric']")
    page.wait_for_function("document.querySelector(\"[data-setting='units'][data-value='metric']\")"
                           ".getAttribute('aria-pressed') === 'true'")
    assert settings.load().language == "en-US" and settings.load().units == "metric"


def test_the_page_starts_in_the_saved_language(browser, tmp_path, language):
    language("en-GB")
    srv = Server(None, cwd=tmp_path)
    ctx = browser.new_context()
    try:
        p = ctx.new_page()
        p.goto(srv.url)
        p.wait_for_function("window.__scrimmage && __scrimmage.mode")
        assert p.get_attribute("html", "lang") == "en-GB"
        assert p.evaluate("import('./i18n.js').then((m) => m.t('Color'))") == "Colour"
    finally:
        ctx.close()
        srv.stop()
