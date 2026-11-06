"""The Scenario, Bench and Measure tabs in another language, in headless Chromium (sub-project 20b, Task 5): each tab's
own words, and the words of its parts built once (the run panel, the shape editor), follow the Language setting live;
a box being typed in keeps its text and focus; nothing is written to the file.

The catalogs are the tests' own, swapped in through the settings answer (test_i18n_pages_browser.py's catalog()), so
they can change words en-GB spells the same. The fixtures live in the tabs' own browser tests."""
import pytest

from scrimmage.bench import session
from test_assembler_browser import browser, scenario_page  # noqa: F401  (fixtures)
from test_bench_browser import _fake_results, _on_stage, make_bench_page  # noqa: F401  (fixtures)
from test_i18n_pages_browser import catalog, english
from test_measure_browser import _pick, measure_page  # noqa: F401  (fixtures)

pytestmark = pytest.mark.browser


def test_the_scenario_tab_follows_the_language(scenario_page):
    page, path = scenario_page("fake_pass.toml")
    before = path.read_bytes()
    assert "Add step" in page.inner_text("#timeline")
    catalog(page, {
        "Add step": "Add a step",
        "Mode": "Mode of play",
        "{count} step · {time} in all|{count} steps · {time} in all":
            {"one": "{count} step, {time} altogether", "other": "{count} steps, {time} altogether"},
        "noun\u0004Run": "Running it",
        "Recent runs": "Earlier runs",
        "▶ Run": "▶ Go",
        "Speed for this run": "This run's pace",
    })
    page.wait_for_function("document.querySelector('#timeline').innerText.includes('Add a step')")
    assert "Mode of play" in page.inner_text("#step-editor")
    assert "altogether" in page.inner_text("#sc-total")
    # the run panel is built once: its words wrote themselves again
    assert page.inner_text("#run-btn") == "▶ Go"
    assert page.text_content("#sc-run .col-title") == "Running it"
    assert page.get_attribute("#run-speed", "aria-label") == "This run's pace"
    english(page)
    page.wait_for_function("document.querySelector('#run-btn').innerText === '▶ Run'")
    assert "Add step" in page.inner_text("#timeline") and "Mode" in page.inner_text("#step-editor")
    assert path.read_bytes() == before


def test_the_bench_tab_follows_the_language(make_bench_page):
    def before(folder):
        out = _fake_results(folder, "bounce_bar", "bounce bar (Priya)", ["3 of 3 got out through `goal`"])
        session.log_event(folder, "run", [folder / "bounce_bar.toml"], out, "agent", True)
    page = make_bench_page(before)
    _on_stage(page, "bounce bar")
    page.wait_for_function("/the agent ran this/.test(document.querySelector('#bench-changed').textContent)")
    catalog(page, {
        "the agent": "the helper",
        "{who} ran this {ago}": "{who} ran it {ago}",
        "Show all": "Show everything",
        "Run": "Go",
        "Other ideas in this folder show here.": "The folder's other ideas show here.",
    })
    page.wait_for_function("/the helper ran it/.test(document.querySelector('#bench-changed').textContent)")
    assert page.inner_text("#bench-showall") == "Show everything"
    assert page.inner_text("#bench-run") == "Go"
    english(page)
    page.wait_for_function("/the agent ran this/.test(document.querySelector('#bench-changed').textContent)")
    assert page.inner_text("#bench-run") == "Run"


def test_the_measure_tab_follows_the_language_and_keeps_a_box_being_typed(measure_page):
    page, path = measure_page()
    _pick(page, "squeeze")
    page.click("#measure-add")
    box = page.locator("#measure-trials tbody tr[data-trial] input[data-reading=force]").last
    box.fill("9.")
    box.focus()
    catalog(page, {
        "Measured by": "Measurer",
        "trial": "go",
        "✓ All changes saved": "✓ Saved",
        "Saving…": "✓ Saved",
        "Not saved: {why}": "Not saved yet: {why}",
    })
    page.wait_for_function("document.querySelector('label[for=measure-by]').textContent === 'Measurer'")
    assert page.inner_text("#measure-trials thead th") == "go"
    # the box being typed in kept its text and its focus
    focused = page.evaluate("document.activeElement && document.activeElement.dataset.reading")
    assert focused == "force"
    assert page.evaluate("document.activeElement.value") == "9."
    english(page)
    page.wait_for_function("document.querySelector('label[for=measure-by]').textContent === 'Measured by'")
