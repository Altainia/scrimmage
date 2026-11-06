"""Browser tests of the Measure tab (`scrimmage edit <name>.measure.toml`), headless Chromium through Playwright.

The Server helper lives in tests/test_assembler_browser.py, the shared Chromium in tests/browser_harness.py.
"""
import math
import tomllib

import pytest

from measure_helpers import synthetic
from scrimmage import units
from scrimmage.measure import fit_cylinder, fit_launcher, forms, stats
from scrimmage.measure.file import load
from test_assembler_browser import Server, browser  # noqa: F401  (fixtures)

pytestmark = pytest.mark.browser

READY = "window.__scrimmageMeasure && __scrimmageMeasure.store.state && document.querySelector('#measure-trials')"
LB = 4.4482216152605

BALL = """\
# the practice bin's ball
[measure]
kind = "ball"
about = "a FUEL ball from the practice bin"
by = "J. Smith"
date = 2026-11-10
tools = "bathroom scale, two boards"
base = "2026-fuel"
"""


@pytest.fixture
def measure_page(browser, tmp_path, monkeypatch):
    """open(path=None, text=BALL) -> (page, the measurement file); everything is torn down afterwards."""
    monkeypatch.setenv("SCRIMMAGE_SKIP_JAVA", "1")
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    servers, contexts, logs = [], [], []

    def open_(path=None, text=BALL, scheme="light"):
        if path is None:
            path = tmp_path / "fuel.measure.toml"
            path.write_text(text, encoding="utf-8")
        srv = Server(path, cwd=tmp_path)
        servers.append(srv)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900}, color_scheme=scheme)
        contexts.append(ctx)
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        page.on("console", lambda m: logs.append(f"console.{m.type}: {m.text}")
                if m.type in ("error", "warning") and "status of 4" not in m.text else None)
        page.goto(srv.url)
        page.wait_for_function(READY)
        return page, path

    yield open_
    for c in contexts:
        c.close()
    for s in servers:
        s.stop()
    assert not logs, f"browser errors: {logs}"


def _pick(page, test):
    page.click(f"#measure-tests [data-test={test}]")
    page.wait_for_function(f"__scrimmageMeasure.store.test === '{test}'")


def _type_trials(page, values, reading="force"):
    for v in values:
        page.click("#measure-add")
        page.locator(f"#measure-trials tbody tr[data-trial] input[data-reading={reading}]").last.fill(str(v))


def _settled(page):
    """Wait for the summary of what is typed now (it is asked for 300 ms after the last change)."""
    page.evaluate("new Promise((r) => setTimeout(r, 400)).then(() => __scrimmageMeasure.summarized)")


def test_the_tab_opens_on_the_file_and_shows_its_tests(measure_page):
    page, _ = measure_page()
    assert page.evaluate("__scrimmage.tab") == "measure" and page.locator("#tab-btn-measure").is_visible()
    assert not page.locator("#tab-btn-robot").is_visible()
    assert page.locator("#measure-tests [data-test]").evaluate_all("els => els.map(e => e.dataset.test)") == \
        ["size", "squeeze", "drop"]
    assert page.inner_text("#file-path").startswith("fuel.measure.toml")
    assert page.inner_text("#measure-saved") == "✓ All changes saved"
    _pick(page, "squeeze")
    assert page.inner_text("#measure-title") == forms.TEST["squeeze"].title
    assert page.locator("#measure-picture svg").count() == 1
    steps = page.locator("#measure-steps li").all_inner_texts()
    assert steps == list(forms.TEST["squeeze"].steps)
    assert page.input_value("#measure-by") == "J. Smith" and page.input_value("#measure-date") == "2026-11-10"
    assert page.input_value("#measure-base") == "2026-fuel"
    assert not page.locator("#measure-fit").is_visible()   # a squeeze is averaged, not fit


def test_five_squeezes_show_their_average_then_an_outlier_is_flagged(measure_page):
    page, path = measure_page()
    _pick(page, "squeeze")
    lbs = [18.8, 19.0, 19.2, 18.9, 19.1]
    _type_trials(page, lbs)
    _settled(page)
    want = stats.summary([x * LB for x in lbs])
    summary = page.inner_text("#measure-summary")   # in the Units setting: Imperial
    assert f"average {want.mean / LB:.2f} lbf" in summary and "spread 2% (5 trials)" in summary, summary
    assert "Good enough to use" in summary
    values = page.locator("#measure-trials td.measure-value").all_inner_texts()
    assert values == [f"{x:.2f} lbf" for x in lbs]
    assert page.locator(".measure-off-flag").count() == 0
    # a sixth trial far off: flagged, kept, and the spread warns
    _type_trials(page, [30.0])
    _settled(page)
    flagged = page.locator("#measure-trials tr.measure-off")
    assert flagged.count() == 1 and flagged.get_attribute("data-trial") == "5"
    assert "looks off: check it" in flagged.inner_text()
    summary = page.inner_text("#measure-summary")
    assert "trial 6 looks off: check it" in summary and "Not good enough yet" in summary
    page.evaluate("__scrimmageMeasure.saved")
    assert [r["force_lb"] for r in load(path).trials["squeeze"]] == [*lbs, 30]   # flagged, never dropped


def test_a_new_columns_unit_follows_the_units_setting(measure_page):
    """A reading's unit picker stays (it is the file's key), and a column with nothing typed yet starts in the Units
    setting's unit: in and lb in Imperial, mm, kg and N in Metric. A column with trials keeps its unit, and the
    summary is shown again in the new units."""
    from browser_harness import page_units
    page, path = measure_page()
    unit = lambda r: page.input_value(f"#measure-trials th select[data-unit={r}]")   # noqa: E731
    _pick(page, "size")
    assert (unit("mass"), unit("circumference")) == ("lb", "in")
    _pick(page, "squeeze")
    assert unit("force") == "lb" and page.input_value("#measure-setup select[data-unit=tare]") == "lb"
    page_units(page, "metric")
    page.wait_for_function("document.querySelector('#measure-trials th select[data-unit=force]').value === 'n'")
    assert page.input_value("#measure-setup select[data-unit=tare]") == "n"
    _pick(page, "size")
    assert (unit("mass"), unit("circumference")) == ("kg", "mm")
    _type_trials(page, [0.215, 0.217], reading="mass")
    _settled(page)
    assert "average 0.2160 kg" in page.inner_text("#measure-summary")
    page_units(page, "imperial")   # the mass column has trials: it stays in kg; the empty one follows
    page.wait_for_function("document.querySelector('#measure-trials th select[data-unit=circumference]').value === 'in'")
    assert unit("mass") == "kg"
    _settled(page)
    page.wait_for_function("document.querySelector('#measure-summary').textContent.includes('average 0.476 lb')")
    page.evaluate("__scrimmageMeasure.saved")
    assert tomllib.loads(path.read_text(encoding="utf-8"))["size"] == [{"mass_kg": 0.215}, {"mass_kg": 0.217}]


def _saved_on_its_own(page):
    """Wait for the save that follows typing (600 ms after the last change), without asking for it."""
    page.wait_for_function("document.querySelector('#measure-saved').textContent === 'Saving…'")
    page.wait_for_function("document.querySelector('#measure-saved').textContent === '✓ All changes saved'")


def test_typed_trials_save_on_their_own_and_a_reload_shows_them(measure_page):
    page, path = measure_page()
    _pick(page, "squeeze")
    _type_trials(page, [9.8, 9.9, 10.0])
    page.select_option("#measure-trials th select[data-unit=force]", "kg")
    page.locator("#measure-tools").press_sequentially(" ")   # still typing in a box: not saved until it stops
    page.fill("#measure-tools", "the blue bathroom scale")
    _saved_on_its_own(page)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# the practice bin's ball\n")
    raw = tomllib.loads(text)
    assert raw["squeeze"] == [{"force_kg": 9.8}, {"force_kg": 9.9}, {"force_kg": 10.0}]
    assert raw["measure"]["tools"] == "the blue bathroom scale"
    page.reload()
    page.wait_for_function(READY)
    _pick(page, "squeeze")
    assert page.locator("#measure-trials input[data-reading=force]").evaluate_all("els => els.map(e => e.value)") == \
        ["9.8", "9.9", "10"]
    assert page.input_value("#measure-trials th select[data-unit=force]") == "kg"
    assert page.input_value("#measure-tools") == "the blue bathroom scale"
    assert "average" in page.inner_text("#measure-summary")
    # removing a trial takes it out of the file
    page.click("#measure-trials button[aria-label='Remove trial 2']")
    _saved_on_its_own(page)
    assert [r["force_kg"] for r in load(path).trials["squeeze"]] == [9.8, 10.0]


def test_print_opens_the_blank_form(measure_page):
    page, _ = measure_page()
    _pick(page, "drop")
    with page.context.expect_page() as info:
        page.click("#measure-print")
    form = info.value
    form.wait_for_load_state()
    assert form.title() == "Ball drop form: Scrimmage"
    assert form.content().count("<script") == 0 and form.locator("table.trials tr").count() == forms.ROWS + 1


def test_leaving_a_box_saves_it_at_once(measure_page):
    page, path = measure_page()
    _pick(page, "size")
    _type_trials(page, [0.31], reading="mass")   # a new column in the Units setting's unit: lb
    page.click("#start-link")   # the box loses focus: saved now, before the start page could open another file
    # well inside the 600 ms the save would wait after typing stops
    page.wait_for_function("document.querySelector('#measure-saved').textContent === '✓ All changes saved'",
                           timeout=450)
    assert load(path).trials["size"] == [{"mass_lb": 0.31}]
    page.click("#start-back")
    assert page.input_value("#measure-trials input[data-reading=mass]") == "0.31"


def test_measure_something_makes_a_file_and_opens_it(measure_page, tmp_path):
    page, _ = measure_page()
    page.click("#start-link")
    page.click("[data-key=measure]")
    page.select_option("#start-measure-kind", "friction")
    page.fill("#start-measure-name", "tread")
    page.click("#start-measure-create")
    page.wait_for_function("window.__scrimmageMeasure && __scrimmageMeasure.store.state && "
                           "__scrimmageMeasure.store.state.kind === 'friction'")
    assert load(tmp_path / "tread.measure.toml").kind == "friction"
    assert page.locator("#measure-tests [data-test]").evaluate_all("els => els.map(e => e.dataset.test)") == ["sled"]
    assert page.inner_text("#measure-title") == forms.TEST["sled"].title


def test_fit_shows_the_fit_and_each_shot_against_the_model(measure_page, tmp_path):
    mf = synthetic(tmp_path, 120.0, 0.95, [50.0, 80.0])
    page, _ = measure_page(mf.path)
    assert page.evaluate("__scrimmageMeasure.store.test") == "shot"
    assert page.locator("#measure-fit").is_visible()
    page.click("#measure-fit")
    page.wait_for_function("document.querySelector('#measure-fit-result .measure-verdict')", timeout=60000)
    want = fit_launcher.fit(mf)
    text = page.inner_text("#measure-fit-result")
    assert want.band_text in text and "Good enough to use" in text
    assert "Fits the ball's stiffness to your shots (the wheel friction held at 0.95)." in text
    rows = page.locator("#measure-fit-result tbody tr").all_inner_texts()
    assert len(rows) == 2 and units.fmt(want.per_shot[0][0], "speed") in rows[0]   # ft/s: the Units setting
    assert all(math.isfinite(m) for m, _ in want.per_shot)
    assert "lbf" in want.band_text
    # a Units change fits again, so the fit's words (the server's) are in the new units
    from browser_harness import page_units
    page_units(page, "metric")
    page.wait_for_function("document.querySelector('#measure-fit-result').textContent.includes(' N fits your shots')",
                           timeout=60000)
    assert "m/s" in page.locator("#measure-fit-result tbody tr").first.inner_text()
    assert "press Fit again" not in page.inner_text("#measure-fit-result")
    # a change to the shots says the fit is old
    page.locator("#measure-trials input[data-reading=wheel_rps]").first.fill("51")
    assert "press Fit again" in page.inner_text("#measure-fit-result")
    # and a change made while it fits does too, once it is done
    page.click("#measure-fit")
    page.locator("#measure-trials input[data-reading=wheel_rps]").first.fill("50")
    page.wait_for_function("!__scrimmageMeasure.store.fitting", timeout=60000)
    assert "press Fit again" in page.inner_text("#measure-fit-result")


def test_a_units_change_while_it_fits_fits_again_when_done(measure_page, tmp_path):
    # A5: the fit under way was asked in the old units; the refit waits for it instead of skipping it
    from browser_harness import page_units
    mf = synthetic(tmp_path, 120.0, 0.95, [50.0, 80.0])
    page, _ = measure_page(mf.path)
    page.click("#measure-fit")
    page.wait_for_function("document.querySelector('#measure-fit-result .measure-verdict')", timeout=60000)
    assert " lbf fits your shots" in page.inner_text("#measure-fit-result")
    page.click("#measure-fit")
    assert page.evaluate("__scrimmageMeasure.store.fitting")
    page_units(page, "metric")
    page.wait_for_function("!__scrimmageMeasure.store.fitting && "
                           "document.querySelector('#measure-fit-result').textContent.includes(' N fits your shots')",
                           timeout=60000)
    assert "m/s" in page.locator("#measure-fit-result tbody tr").first.inner_text()


def test_the_tab_reads_in_the_dark_theme(measure_page):
    page, _ = measure_page(scheme="dark")
    _pick(page, "squeeze")
    bg = page.evaluate("getComputedStyle(document.querySelector('#measure-picture')).backgroundColor")
    assert bg == "rgb(255, 255, 255)"   # the black-on-white picture keeps its white card


def test_fit_shows_a_cylinder_s_fit_against_its_strokes(measure_page, tmp_path):
    p = tmp_path / "deploy.measure.toml"
    p.write_text('[measure]\nkind = "cylinder"\nby = "J. Smith"\ndate = 2026-11-10\n\n[stroke_setup]\n'
                 "bore_in = 1.0625\nrod_in = 0.3125\nstroke_in = 6\nload_mass_lb = 2\nvertical = true\nfps = 240\n\n"
                 "[[stroke]]\ntime_s = 0.31\n\n[[stroke]]\nframes = 74\n\n[[stroke]]\ntime_s = 0.30\n",
                 encoding="utf-8")
    page, _ = measure_page(p)
    assert page.input_value("#measure-setup select[data-reading=vertical]") == "true"
    assert "time: average 0.306 s, lowest 0.300 s, highest 0.310 s, spread 3% (3 trials)" in \
        page.inner_text("#measure-summary")
    page.click("#measure-fit")
    page.wait_for_function("document.querySelector('#measure-fit-result .measure-verdict')", timeout=60000)
    want = fit_cylinder.fit(load(p))
    text = page.inner_text("#measure-fit-result")
    assert want.band_text in text, text
    assert f"the model's takes {want.model_time:.3f} s, off by {want.error * 100:+.1f}%" in text


def test_an_outside_edit_while_a_change_waits_shows_the_file_and_says_so(measure_page):
    page, path = measure_page(text=BALL + "\n[[squeeze]]\nforce_lb = 19.0\n")
    _pick(page, "squeeze")
    page.fill("#measure-trials input[data-reading=force]", "18.0")   # waits 600 ms to be saved
    path.write_text(BALL + "\n[[squeeze]]\nforce_lb = 21.5\n", encoding="utf-8")
    # the waiting save is refused (409, the file changed after the page read it): the page shows the file as saved
    page.wait_for_function("[...document.querySelectorAll('#notices .notice')].some((n) => "
                           "n.textContent.includes('so it shows the saved version'))")
    page.wait_for_function("document.querySelector('#measure-trials input[data-reading=force]').value === '21.5'")
    assert page.inner_text("#measure-saved") == "✓ All changes saved"
    page.evaluate("__scrimmageMeasure.saved")
    assert load(path).trials["squeeze"] == [{"force_lb": 21.5}]   # the outside edit stands


def test_a_change_waiting_to_save_is_sent_when_the_page_goes_away(measure_page):
    page, path = measure_page()
    _pick(page, "size")
    _type_trials(page, [0.31], reading="mass")
    page.goto("about:blank")   # well inside the 600 ms wait: pagehide sends it
    for _ in range(50):
        if "size" in load(path).trials:
            break
        page.wait_for_timeout(100)
    assert load(path).trials["size"] == [{"mass_lb": 0.31}]
