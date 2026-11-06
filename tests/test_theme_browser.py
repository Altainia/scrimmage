"""The Theme setting (System, Light or Dark) in headless Chromium: the Settings menu's row, theme.js and every page
reader of the theme (Blockly, the 3D views' backgrounds, the Test tab's chart), the light fallback for browsers
without light-dark(), and the radius tokens (L8).

The fixtures (Chromium lookup, the Server helper) live in tests/test_assembler_browser.py.
"""
import re
from pathlib import Path

import pytest

import test_part_browser as part_tests
import test_test_browser as test_tests
from scrimmage import settings
from test_assembler_browser import Server, browser, make_page  # noqa: F401  (fixtures)
from test_bench_browser import make_bench_page  # noqa: F401  (fixture)
from test_drive_browser import drive_page  # noqa: F401  (fixture)

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "scrimmage" / "assembler" / "static"
LIGHT_BG, DARK_BG = "rgb(244, 245, 247)", "rgb(13, 17, 23)"     # app.css --bg, light and dark


@pytest.fixture
def open_page(browser, tmp_path):
    """open(scheme="light") -> the start page in a browser whose computer is in `scheme`, with window.T = theme.js."""
    servers, contexts, logs = [], [], []

    def open_(scheme="light"):
        srv = Server(None, cwd=tmp_path)
        servers.append(srv)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900}, color_scheme=scheme)
        contexts.append(ctx)
        p = ctx.new_page()
        p.set_default_timeout(20000)
        p.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        p.on("console", lambda m: logs.append(f"console.{m.type}: {m.text}")
             if m.type in ("error", "warning") else None)
        p.goto(srv.url)
        p.wait_for_function("window.__scrimmage && __scrimmage.mode")
        p.evaluate("""async () => {
          window.T = (await import('./theme.js')).theme;
          window.changes = 0;
          T.onChange(() => { window.changes++; });
        }""")
        return p

    yield open_
    for c in contexts:
        c.close()
    for s in servers:
        s.stop()
    assert not logs, f"browser errors: {logs}"


def _bg(page):
    return page.evaluate("getComputedStyle(document.body).backgroundColor")


def _choose(page, value):
    page.click("#settings-btn")
    page.click(f"#settings-theme [data-value='{value}']")
    page.wait_for_function(f"document.documentElement.dataset.theme === {value!r}")
    page.keyboard.press("Escape")


def test_each_choice_applies_and_is_saved(open_page):
    page = open_page("light")
    assert page.inner_text("#settings-theme-label") == "Theme"
    assert [page.inner_text(f"#settings-theme [data-value='{v}']") for v in settings.THEMES] == \
        ["System", "Light", "Dark"]
    assert page.evaluate("document.documentElement.dataset.theme") == "system" and _bg(page) == LIGHT_BG
    for value, bg, dark in (("dark", DARK_BG, True), ("light", LIGHT_BG, False), ("system", LIGHT_BG, False)):
        _choose(page, value)
        assert _bg(page) == bg and page.evaluate("T.isDark()") is dark and page.evaluate("T.choice") == value
        assert settings.load().theme == value
        assert page.get_attribute(f"#settings-theme [data-value='{value}']", "aria-pressed") == "true"
        assert page.evaluate("getComputedStyle(document.documentElement).colorScheme") == \
            {"system": "light dark", "light": "light", "dark": "dark"}[value]
    assert page.evaluate("window.changes") == 3


def test_system_follows_the_computer_live(open_page):
    page = open_page("light")
    page.emulate_media(color_scheme="dark")
    page.wait_for_function("window.changes === 1")
    assert _bg(page) == DARK_BG and page.evaluate("T.isDark()") is True
    assert page.evaluate("T.color('--bg')") == "#0d1117"
    page.emulate_media(color_scheme="light")
    page.wait_for_function("window.changes === 2")
    assert _bg(page) == LIGHT_BG and page.evaluate("T.color('--bg')") == "#f4f5f7"


def test_light_or_dark_ignores_the_computer(open_page):
    page = open_page("light")
    _choose(page, "light")
    page.emulate_media(color_scheme="dark")
    page.wait_for_timeout(200)
    assert _bg(page) == LIGHT_BG and page.evaluate("T.isDark()") is False and page.evaluate("window.changes") == 1
    _choose(page, "dark")
    page.emulate_media(color_scheme="light")
    page.wait_for_timeout(200)
    assert _bg(page) == DARK_BG and page.evaluate("T.isDark()") is True and page.evaluate("window.changes") == 2


def test_the_saved_theme_is_there_from_the_start_and_a_change_elsewhere_arrives(open_page):
    settings.save(theme="dark")
    page = open_page("light")
    assert page.evaluate("document.documentElement.dataset.theme") == "dark" and _bg(page) == DARK_BG
    assert page.evaluate("window.changes") == 0      # already in it: nothing to change
    settings.save(theme="system")                    # another editor, the CLI or by hand: the poll brings it
    page.wait_for_function("document.documentElement.dataset.theme === 'system'", timeout=8000)
    assert _bg(page) == LIGHT_BG


def test_color_reads_a_token_as_shown(open_page):
    page = open_page("dark")
    assert page.evaluate("T.color('--accent-text')") == "#6cb6ff"
    assert page.evaluate("T.color('--overlay')") == "rgba(22, 27, 34, 0.92)"
    assert page.evaluate("T.color('--field-blue', 0.16)") == "rgba(68, 147, 248, 0.16)"
    assert page.evaluate("T.color('--feed-in')") == "#6cb6ff"          # a token naming another
    _choose(page, "light")
    assert page.evaluate("T.color('--accent-text')") == "#2b6cb0"
    assert page.evaluate("T.color('--field-surround')") == "#dfe3e8"   # the light drawing's colors as they were


def test_an_older_save_answered_last_still_shows_its_setting(open_page):
    """Units saved, then Language saved and answered first: the Units answer, arriving after, still shows Metric
    (settings.js keeps the latest answer per setting, not just the latest answer)."""
    page = open_page("light")
    held = []

    def hold(route):
        body = route.request.post_data or ""
        if route.request.method == "POST" and '"units"' in body:
            held.append(route)          # answered below, after the Language save
        elif route.request.method == "GET":
            pass                        # the poll waits too, so only the saves' answers can change the menu
        else:
            route.continue_()
    page.route("**/api/settings*", hold)
    page.click("#settings-btn")
    page.click("#settings-menu [data-setting='units'][data-value='metric']")
    for _ in range(50):
        if held:
            break
        page.wait_for_timeout(50)
    assert held
    page.click("#settings-language [data-value='en-GB']")
    page.wait_for_function("document.documentElement.lang === 'en-GB'")
    assert page.get_attribute("[data-setting='units'][data-value='imperial']", "aria-pressed") == "true"
    held[0].continue_()
    page.wait_for_function("document.querySelector(\"[data-setting='units'][data-value='metric']\")"
                           ".getAttribute('aria-pressed') === 'true'", timeout=5000)
    assert page.evaluate("document.documentElement.lang") == "en-GB"
    assert settings.load().units == "metric" and settings.load().language == "en-GB"


# -- every reader of the theme follows a switch ---------------------------------------------------------------------
def _save_theme(page, value):
    assert page.evaluate("(v) => import('./theme.js').then((m) => m.setTheme(v))", value) is None


@pytest.fixture
def test_tab(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("SCRIMMAGE_SKIP_JAVA", "1")
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    srv = Server(test_tests._write(tmp_path, test_tests.SPIN), cwd=tmp_path)
    ctx = browser.new_context(viewport={"width": 1500, "height": 950}, color_scheme="light")
    logs = []
    page = ctx.new_page()
    page.set_default_timeout(30000)
    page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
    page.on("console", lambda m: logs.append(f"console.{m.type}: {m.text}")
            if m.type in ("error", "warning") and "status of 4" not in m.text else None)
    page.goto(srv.url)
    page.wait_for_function(test_tests.READY)
    yield page
    ctx.close()
    srv.stop()
    assert not logs, f"browser errors: {logs}"


CORNER = """() => {
  const c = document.querySelector('#test-chart canvas'), dpr = window.devicePixelRatio || 1;
  const copy = Object.assign(document.createElement('canvas'), {width: c.width, height: c.height});
  const ctx = copy.getContext('2d', {willReadFrequently: true});
  ctx.drawImage(c, 0, 0);
  const [r, g, b] = ctx.getImageData(Math.round(2 * dpr), Math.round(2 * dpr), 1, 1).data;
  return '#' + [r, g, b].map((x) => x.toString(16).padStart(2, '0')).join('');
}"""


def test_blockly_and_the_chart_follow_a_switch(test_tab):
    page = test_tab
    test_tests._run(page, "001")
    assert page.evaluate("__scrimmageTest.workspace.getTheme().name") == "scrimmage-light"
    assert page.evaluate("__scrimmageTest.replay.chart.colors") == \
        {"run": "#2b6cb0", "compare": "#7d8590", "bg": "#f6f8fa"}
    assert page.evaluate(CORNER) == "#f6f8fa"
    _save_theme(page, "dark")
    page.wait_for_function("__scrimmageTest.workspace.getTheme().name === 'scrimmage-dark'")
    assert page.evaluate("__scrimmageTest.workspace.getTheme().componentStyles.workspaceBackgroundColour") == "#161b22"
    assert page.evaluate("__scrimmageTest.replay.chart.colors") == \
        {"run": "#6cb6ff", "compare": "#768390", "bg": "#1c2129"}
    assert page.evaluate(CORNER) == "#1c2129"          # redrawn in the dark colors
    _save_theme(page, "light")
    page.wait_for_function("__scrimmageTest.workspace.getTheme().name === 'scrimmage-light'")
    assert page.evaluate(CORNER) == "#f6f8fa"


def test_the_part_tabs_3d_background_follows_a_switch(browser, tmp_path, monkeypatch):
    page, _, close = part_tests._open(browser, tmp_path, monkeypatch, "climber.step")
    try:
        assert page.evaluate("__scrimmagePart.scene.background") == "#eef0f3"      # --sunken
        _save_theme(page, "dark")
        page.wait_for_function("__scrimmagePart.scene.background === '#0f141a'")
        page.emulate_media(color_scheme="dark")
        _save_theme(page, "system")                  # System on a dark computer: still dark
        page.wait_for_function("document.documentElement.dataset.theme === 'system'")
        assert page.evaluate("__scrimmagePart.scene.background") == "#0f141a"
        page.emulate_media(color_scheme="light")
        page.wait_for_function("__scrimmagePart.scene.background === '#eef0f3'")
    finally:
        close()


WATCH_LIGHT, WATCH_DARK = "#e9ecf0", "#0f141a"     # app.css --watch-bg


def _follows(page, hook):
    """The 3D view whose background `hook` reads goes dark and back with the setting."""
    assert page.evaluate(hook) == WATCH_LIGHT
    _save_theme(page, "dark")
    page.wait_for_function(f"{hook} === {WATCH_DARK!r}")
    _save_theme(page, "light")
    page.wait_for_function(f"{hook} === {WATCH_LIGHT!r}")


def test_the_assembler_3d_background_follows_a_switch(make_page):
    page, _ = make_page(None, "--new", "swerve")
    _follows(page, "__scrimmage.scene.background")


def test_the_drive_3d_background_follows_a_switch(drive_page):
    page = drive_page()
    page.wait_for_function("__scrimmage.live3d")
    _follows(page, "__scrimmage.live3d.background")


def test_the_bench_3d_background_follows_a_switch(make_bench_page):
    page = make_bench_page()
    page.click("#bench-edit")
    page.wait_for_function("__scrimmageBench.editor && __scrimmageBench.editor.instances.length > 0")
    _follows(page, "__scrimmageBench.editor.background")


# -- browsers without light-dark(): the light theme --------------------------------------------------------------
def test_without_light_dark_the_page_is_light(open_page):
    """app.css with its @supports test made false (as in a browser without light-dark()): every token is its light
    value, even with Dark chosen, and the colors still work."""
    page = open_page("dark")
    _choose(page, "dark")
    assert _bg(page) == DARK_BG
    page.evaluate("""async () => {
      const css = await (await fetch('app.css')).text();
      const old = css.replace('@supports (color: light-dark(#000, #fff))', '@supports (color: no-such-color(1))');
      if (old === css) throw new Error('no @supports block');
      document.head.append(Object.assign(document.createElement('style'), { textContent: old }));
      document.querySelector('link[href="app.css"]').remove();
    }""")
    assert page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()") == "#f4f5f7"
    assert _bg(page) == LIGHT_BG
    assert page.evaluate("getComputedStyle(document.body).color") == "rgb(31, 35, 40)"      # --text, light
    assert page.evaluate("T.color('--field-surround')") == "#dfe3e8"


# -- the sources: tokens and radii ----------------------------------------------------------------------------------
def _css() -> str:
    return (STATIC / "app.css").read_text(encoding="utf-8")


def test_canvas_colors_are_tokens():
    """Every color a script draws with is an app.css token read through theme.color, so it follows the theme. The
    exceptions are data, not theme: the Part tab's body colors and the world's own geom colors (from the model)."""
    tokens = set(re.findall(r"^\s*(--[\w-]+):", _css(), re.M)) | set(re.findall(r";\s*(--[\w-]+):", _css()))
    allowed = {("part.js", "PALETTE"), ("fieldpick.js", "o.poly"), ("theme.js", ""),
               ("test_run.js", 'return "#ffffff"')}
    # three.js colors given as numbers: the selection and marker tints stay as they are in both themes (bright
    # enough on either background, and the same as the 2D drawings' accents)
    numeric_allowed = {("view3d.js", "HOVER"), ("view3d.js", "SELECTED"), ("view3d.js", "PICK"), ("view3d.js", "CG"),
                       ("bench_edit.js", "HOVER"), ("bench_edit.js", "SELECTED"), ("part3d.js", "SELECTED")}
    found = []
    for js in sorted(STATIC.glob("*.js")):
        for n, line in enumerate(js.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//")[0]
            if re.search(r"""["'`]#[0-9a-fA-F]{3,8}(?![\w-])|rgba?\(""", code) and \
                    not any(js.name == f and marker in line for f, marker in allowed):
                found.append(f"{js.name}:{n}: {line.strip()}")
            three = re.search(r"THREE\.Color\(|setClearColor|\.background\b", code)
            if three and re.search(r"0x[0-9a-fA-F]{6}", code) and \
                    not any(js.name == f and re.match(rf"\s*const {marker} =", line) for f, marker in numeric_allowed):
                found.append(f"{js.name}:{n}: {line.strip()}")
            for name in re.findall(r"""(?:color|\bc|robotColor)\(\s*["'](--[\w-]+)""", code):
                assert name in tokens, f"{js.name}:{n}: {name} isn't in app.css"
    assert found == []


def test_radii_are_the_three_tokens():
    css = _css()
    assert re.findall(r"--radius(-sm|-lg)?:\s*([^;]+);", css) == [("-sm", "2px"), ("", "4px"), ("-lg", "6px")]
    for value in re.findall(r"border(?:-[a-z]+)*-radius:\s*([^;}]+)", css):
        for part in value.split():
            assert part in ("0", "50%", "var(--radius-sm)", "var(--radius)", "var(--radius-lg)"), value
    gallery = (ROOT / "scrimmage" / "bench" / "gallery.py").read_text(encoding="utf-8")
    assert re.findall(r"border-radius:\s*([^;]+);", gallery) == ["2px"]   # its standalone CSS: --radius-sm


def test_the_dark_values_are_written_once():
    """The tokens hold light-dark(light, dark), and the theme is the root's color-scheme: no second block of dark
    values to keep in step with the first. The plain values before it (the fallback where light-dark() doesn't
    work) are each pair's light half, so the two can't drift."""
    css = re.sub(r"/\*.*?\*/", "", _css(), flags=re.S)
    plain = css[css.index(":root {"):css.index("\n}\n", css.index(":root {"))]
    block = css[css.index("@supports (color: light-dark(#000, #fff)) {"):]
    block = block[:block.index("\n}\n")]
    value = r"(?:rgba?\([^)]*\)|[^,()])+?"
    pairs = re.findall(rf"(--[\w-]+):\s*light-dark\(({value}),\s*{value}\);", block)
    assert len(pairs) > 60 and "light-dark(" not in plain
    fallback = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", plain))
    for name, light in pairs:
        assert fallback.get(name) == light, name
    assert "prefers-color-scheme: dark) {\n  :root {" not in css
    assert css.count("light-dark(") > 60
    assert ':root[data-theme="light"] { color-scheme: light; }' in css
    assert ':root[data-theme="dark"] { color-scheme: dark; }' in css
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<html lang="en-US" data-theme="system">' in html
