"""The page's units.js against scrimmage/units.py, and the header's Settings menu, in headless Chromium.

The fixtures (Chromium lookup, the Server helper) live in tests/test_assembler_browser.py.
"""
import pytest

from scrimmage import settings
from scrimmage.units import KINDS, fmt
from test_assembler_browser import Server, browser  # noqa: F401  (fixtures)

pytestmark = pytest.mark.browser

# SI values each kind is formatted at, on both sides: zero, negatives, ties, small and huge values
VALUES = [0.0, 1.0, -1.0, 0.1016, 0.00635, 3.81, 0.125, -0.125, 2700.0, 413685.4, 1e-7, 123456.789, 1e6, -42.42]


@pytest.fixture
def page(browser, tmp_path):
    srv = Server(None, cwd=tmp_path)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    logs = []
    p = ctx.new_page()
    p.set_default_timeout(20000)
    p.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
    p.on("console", lambda m: logs.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    p.goto(srv.url)
    p.wait_for_function("window.__scrimmage && __scrimmage.mode")
    p.evaluate("async () => { window.U = await import('./units.js'); }")
    yield p
    ctx.close()
    srv.stop()
    assert not logs, f"browser errors: {logs}"


def _switch(page, system):
    assert page.evaluate("(s) => U.set(s)", system) is None
    assert page.evaluate("U.units.system") == system


def test_js_formats_as_python_does(page):
    for system in ("imperial", "metric"):
        _switch(page, system)
        got = page.evaluate("([kinds, values]) => kinds.map((k) => values.map((v) => U.fmt(v, k)))", [KINDS, VALUES])
        want = [[fmt(v, k, system) for v in VALUES] for k in KINDS]
        assert got == want, system
    assert page.evaluate("[U.fmt(null, 'length'), U.fmt(undefined, 'length')]") == ["", ""]


# (kind, typed, imperial SI or error text, metric SI or error text): Review Focus 5
PARSE = [
    ("length", "4", 0.1016, 0.004),
    ("length", "4 in", 0.1016, "Units is set to Metric, so type mm here, not in (or change Units in Settings)"),
    ("length", '4"', 0.1016, "Units is set to Metric, so type mm here, not in (or change Units in Settings)"),
    ("length", "100mm", "Units is set to Imperial, so type in here, not mm (or change Units in Settings)", 0.1),
    ("length", "101.6 MM", "Units is set to Imperial, so type in here, not mm (or change Units in Settings)", 0.1016),
    ("length", "-0.25", -0.00635, -0.00025),
    ("length", "0", 0.0, 0.0),
    ("length", "1e6", 25400.0, 1000.0),
    ("length", ".5 in", 0.0127, "Units is set to Metric, so type mm here, not in (or change Units in Settings)"),
    ("length", "4 m", "Units is set to Imperial, so type in here, not m (or change Units in Settings)", 4.0),
    ("length", "4 lb", '"lb" isn\'t a unit for this: type a number in in', '"lb" isn\'t a unit for this: type a number in mm'),
    ("length", "", "Type a number in in", "Type a number in mm"),
    ("length", "four", "Type a number in in", "Type a number in mm"),
    ("field", "12.5 ft", 3.81, "Units is set to Metric, so type m here, not ft (or change Units in Settings)"),
    ("field", "3.81 m", "Units is set to Imperial, so type ft here, not m (or change Units in Settings)", 3.81),
    ("speed", "10 fps", 3.048, "Units is set to Metric, so type m/s here, not ft/s (or change Units in Settings)"),
    ("speed", "3 m/s", "Units is set to Imperial, so type ft/s here, not m/s (or change Units in Settings)", 3.0),
    ("mass", "120 lbs", 54.43108440, "Units is set to Metric, so type kg here, not lb (or change Units in Settings)"),
    ("mass", "2 kg", "Units is set to Imperial, so type lb here, not kg (or change Units in Settings)", 2.0),
    ("force", "5 lbf", 22.2411080763, "Units is set to Metric, so type N here, not lbf (or change Units in Settings)"),
    ("force", "10 N", "Units is set to Imperial, so type lbf here, not N (or change Units in Settings)", 10.0),
    ("torque", "10 in-lb", 1.12984829028, "Units is set to Metric, so type N·m here, not in·lbf (or change Units in Settings)"),
    ("torque", "2 Nm", "Units is set to Imperial, so type in·lbf here, not N·m (or change Units in Settings)", 2.0),
    ("pressure", "60 psi", 413685.437590, "Units is set to Metric, so type kPa here, not psi (or change Units in Settings)"),
    ("pressure", "413.7 kpa", "Units is set to Imperial, so type psi here, not kPa (or change Units in Settings)", 413700.0),
    ("density", "0.0975 lb/in^3", 2698.79070924, "Units is set to Metric, so type g/cm³ here, not lb/in³ (or change Units in Settings)"),
    ("density", "2.7 g/cm3", "Units is set to Imperial, so type lb/in³ here, not g/cm³ (or change Units in Settings)", 2700.0),
    ("spring", "1 lbf/in", 175.126835246, "Units is set to Metric, so type N/m here, not lbf/in (or change Units in Settings)"),
    ("spring", "175 N/m", "Units is set to Imperial, so type lbf/in here, not N/m (or change Units in Settings)", 175.0),
    ("angle", "90 deg", 90.0, 90.0),
    ("angle", "-45°", -45.0, -45.0),
    ("time", "1.5 s", 1.5, 1.5),
    ("current", "40 A", 40.0, 40.0),
    ("voltage", "12", 12.0, 12.0),
]


def test_parse_both_systems(page):
    assert {k for k, *_ in PARSE} == set(KINDS)
    for column, system in ((2, "imperial"), (3, "metric")):
        _switch(page, system)
        got = page.evaluate("(cases) => cases.map(([k, t]) => U.parse(t, k))", [[c[0], c[1]] for c in PARSE])
        for case, g in zip(PARSE, got):
            want = case[column]
            if isinstance(want, str):
                assert g == {"error": want}, (system, case)
            else:
                assert g == pytest.approx(want, rel=1e-9), (system, case)


# Any length unit of the chosen system works in a length or field box (a tolerance typed "6 in", a sensor range
# "2 ft"); the other system's are refused as any other unit of theirs is
LENGTHS = [
    ("field", "6 in", 0.1524, "Units is set to Metric, so type m here, not in (or change Units in Settings)"),
    ("field", '6"', 0.1524, "Units is set to Metric, so type m here, not in (or change Units in Settings)"),
    ("length", "1.5 ft", 0.4572, "Units is set to Metric, so type mm here, not ft (or change Units in Settings)"),
    ("length", "2 cm", "Units is set to Imperial, so type in here, not cm (or change Units in Settings)", 0.02),
    ("length", "0.3 m", "Units is set to Imperial, so type in here, not m (or change Units in Settings)", 0.3),
    ("field", "30 cm", "Units is set to Imperial, so type ft here, not cm (or change Units in Settings)", 0.3),
    ("field", "100 mm", "Units is set to Imperial, so type ft here, not mm (or change Units in Settings)", 0.1),
    ("field", "12", 3.6576, 12.0),
    ("speed", "3 in", '"in" isn\'t a unit for this: type a number in ft/s', '"in" isn\'t a unit for this: type a number in m/s'),
]


def test_a_length_box_takes_any_length_unit_of_the_system(page):
    for column, system in ((2, "imperial"), (3, "metric")):
        _switch(page, system)
        got = page.evaluate("(cases) => cases.map(([k, t]) => U.parse(t, k))", [[c[0], c[1]] for c in LENGTHS])
        for case, g in zip(LENGTHS, got):
            want = case[column]
            if isinstance(want, str):
                assert g == {"error": want}, (system, case)
            else:
                assert g == pytest.approx(want, rel=1e-12), (system, case)


def test_typed_values_round_trip(page):
    # Review Focus 1, at the helper: what is typed reads back as typed, in both systems
    for system, typed, kind in (("imperial", "4", "length"), ("imperial", "0.25", "length"),
                                ("imperial", "12.5", "field"), ("metric", "101.6", "length"),
                                ("imperial", "60", "pressure"), ("metric", "413.7", "pressure")):
        _switch(page, system)
        assert page.evaluate("([t, k]) => U.fmt(U.parse(t, k), k)", [typed, kind]) == f"{typed} " + \
            page.evaluate("(k) => U.name(k)", kind)
    _switch(page, "imperial")
    si = page.evaluate("U.parse('4', 'length')")
    _switch(page, "metric")
    assert page.evaluate("(si) => U.fmt(si, 'length')", si) == "101.6 mm"


def test_file_keys_keep_their_own_unit(page):
    # U3: an `_in` key is written in inches whatever the setting; a measure file's `force_lb` in pounds-force
    for system in ("imperial", "metric"):
        _switch(page, system)
        assert page.evaluate("U.toKey(U.parse(system === 'metric' ? '101.6' : '4', 'length'), 'diameter_in')"
                             .replace("system", repr(system))) == 4.0
        assert page.evaluate("U.fromKey(4, 'in')") == 0.1016
        assert page.evaluate("U.toKey(4.4482216152605, 'force_lb', 'force')") == 1.0
        assert page.evaluate("U.toKey(0.45359237, 'lb', 'mass')") == 1.0
        assert page.evaluate("U.fromKey(90, 'angle_deg')") == 90.0
    assert "say which" in page.evaluate("(() => { try { U.toKey(1, 'lb'); } catch (e) { return e.message; } })()")


def test_settings_menu_switches_and_the_page_rerenders(page):
    # Review Focus 4 (the mechanism): a value shown through units.onChange follows the setting with no reload
    page.evaluate("""() => {
      window.notReloaded = true;
      const el = document.createElement('output');
      el.id = 'demo';
      const show = () => { el.textContent = U.fmt(0.1016, 'length'); };
      U.onChange(show); show();
      document.body.append(el);
    }""")
    assert page.text_content("#demo") == "4 in"
    assert page.get_attribute("#settings-btn", "aria-expanded") == "false"
    page.click("#settings-btn")
    assert page.is_visible("#settings-menu")
    assert page.get_attribute("[data-setting='units'][data-value='imperial']", "aria-pressed") == "true"
    page.click("[data-setting='units'][data-value='metric']")
    page.wait_for_function("document.getElementById('demo').textContent === '101.6 mm'")
    assert page.get_attribute("[data-setting='units'][data-value='metric']", "aria-pressed") == "true"
    assert page.get_attribute("[data-setting='units'][data-value='imperial']", "aria-pressed") == "false"
    assert settings.load().units == "metric"
    page.keyboard.press("Escape")
    assert not page.is_visible("#settings-menu")
    # a change made elsewhere (another editor, the CLI, by hand) arrives by the poll
    (settings.settings_dir() / "settings.toml").write_text('units = "imperial"\n')
    page.wait_for_function("document.getElementById('demo').textContent === '4 in'", timeout=10000)
    assert page.evaluate("window.notReloaded") is True
    page.click("#settings-btn")
    assert page.get_attribute("[data-setting='units'][data-value='imperial']", "aria-pressed") == "true"
    page.click("header .brand")                      # a click elsewhere closes it
    assert not page.is_visible("#settings-menu")


def test_a_bad_settings_file_shows_one_warning(browser, tmp_path):
    f = settings.settings_dir() / "settings.toml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('units = "furlongs"\n')
    srv = Server(None, cwd=tmp_path)
    ctx = browser.new_context()
    try:
        p = ctx.new_page()
        p.goto(srv.url)
        p.wait_for_function("window.__scrimmage && __scrimmage.mode")
        p.wait_for_selector(".notice-warn")
        assert "isn't imperial or metric; using imperial units" in p.text_content(".notice-warn")
        assert p.evaluate("import('./units.js').then((m) => m.units.system)") == "imperial"
    finally:
        ctx.close()
        srv.stop()


def test_a_failed_first_load_catches_up_on_the_next_poll(browser, tmp_path):
    f = settings.settings_dir() / "settings.toml"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('units = "furlongs"\n')
    srv = Server(None, cwd=tmp_path)
    ctx = browser.new_context()
    try:
        p = ctx.new_page()
        failed = []

        def first_fails(route):
            if route.request.method == "GET" and not failed:
                failed.append(1)
                route.abort()
            else:
                route.continue_()
        p.route("**/api/settings*", first_fails)
        p.goto(srv.url)
        p.wait_for_function("window.__scrimmage && __scrimmage.mode")
        assert failed
        p.wait_for_selector(".notice-warn", timeout=15000)     # the 2 s poll found the warning
        assert "isn't imperial or metric" in p.text_content(".notice-warn")
        assert p.evaluate("import('./units.js').then((m) => m.units.fmt(0.1016, 'length'))") == "4 in"
        assert p.locator(".notice-warn").count() == 1
    finally:
        ctx.close()
        srv.stop()


def test_a_poll_answered_after_a_change_doesnt_undo_it(page):
    """A GET that left before set()'s POST but is answered after it carries the old setting: it is dropped."""
    got = page.evaluate("""async () => {
      const real = window.fetch;
      let open; const gate = new Promise((r) => { open = r; });
      window.fetch = async (url, opts) => {
        const res = await real(url, opts);
        if ((opts.method || 'GET') === 'GET') await gate;   // the server answered (Imperial); held back
        return res;
      };
      try {
        const poll = U.load();
        await new Promise((r) => setTimeout(r, 300));
        await U.set('metric');
        open();
        await poll;
        return U.units.system;
      } finally { window.fetch = real; }
    }""")
    assert got == "metric"
    assert page.evaluate("U.load().then(() => U.units.system)") == "metric"
