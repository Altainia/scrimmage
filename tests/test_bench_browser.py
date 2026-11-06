"""Browser tests of the Bench tab's Stage (`scrimmage edit <idea session folder>`), headless Chromium through Playwright.

The fixtures (Chromium lookup, the Server helper) live in tests/test_assembler_browser.py. The session folder holds
copies of examples/bench/bounce_bar.toml and pinch_car.toml; bounce bar is the newer, so it starts on stage.
"""
import json
import os
import re
import shutil
import time
from pathlib import Path

import pytest

from browser_harness import page_units
from scrimmage.bench import session
from test_assembler_browser import REPO, Server, browser  # noqa: F401  (fixtures)

pytestmark = pytest.mark.browser

UNC = "UNCALIBRATED: estimates from preset numbers, not measurements; measure."


def _fake_results(folder: Path, stem: str, title: str, heads: list[str], energy: str = "ok", gif: bool = True) -> Path:
    """A results folder as `bench run` writes it: summary.txt, summary.json and small real pictures."""
    from PIL import Image
    out = folder / f"{stem}-results"
    out.mkdir(exist_ok=True)
    (out / "summary.txt").write_text(f"{title}\nidea: \"x\"\n\n" + "\n".join(heads) + f"\n{UNC}\n\n"
                                     f"Energy check: {energy} (a failure means the simulation is wrong, not the design)\n")
    (out / "summary.json").write_text(json.dumps({"headlines": len(heads) + 1, "uncalibrated": True,
                                                  "energy_failed": energy != "ok"}))
    Image.new("RGB", (40, 20), (200, 30, 30)).save(out / "path_side.png")
    if gif:
        Image.new("RGB", (40, 20), (30, 30, 200)).save(out / "pass.gif")
    return out


@pytest.fixture
def make_bench_page(browser, tmp_path):
    """make_bench_page(before=None) -> page on `scrimmage edit tmp_path/kickoff`; `before(folder)` runs before the
    server starts."""
    servers, contexts, logs = [], [], []

    def make(before=None):
        folder = tmp_path / "kickoff"
        folder.mkdir(exist_ok=True)
        now = time.time()
        for i, name in enumerate(["pinch_car.toml", "bounce_bar.toml"]):
            shutil.copy(REPO / "examples" / "bench" / name, folder / name)
            os.utime(folder / name, (now - 100 + 50 * i, now - 100 + 50 * i))
        if before:
            before(folder)
        srv = Server(folder, cwd=tmp_path)
        servers.append(srv)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        contexts.append(ctx)
        page = ctx.new_page()
        page.set_default_timeout(20000)
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        page.on("console", lambda m: logs.append(f"console.{m.type}: {m.text}") if m.type == "error" and "status of 4" not in m.text else None)
        page.goto(srv.url)
        page.wait_for_function("window.__scrimmageBench && __scrimmageBench.state && __scrimmageBench.shown")
        return page

    yield make
    for c in contexts:
        c.close()
    for s in servers:
        s.stop()
    assert not logs, f"browser errors: {logs}"


def _on_stage(page, name: str, timeout=2000):
    page.wait_for_function("(n) => document.querySelector('#bench-name')?.textContent === n", arg=name, timeout=timeout)


def _first_focus(page) -> str:
    return page.evaluate("document.querySelector('#bench-focus [data-field]')?.dataset.field || ''")


def test_stage_shows_latest_run_with_uncalibrated(make_bench_page):
    def before(folder):
        out = _fake_results(folder, "bounce_bar", "bounce bar (Priya)", ["3 of 3 got out through `goal`", "the bar gives 1 cm"])
        session.log_event(folder, "run", [folder / "bounce_bar.toml"], out, "agent", True)
    page = make_bench_page(before)
    assert page.evaluate("__scrimmage.mode") == "bench"
    _on_stage(page, "bounce bar")
    assert page.text_content("#bench-author") == "Priya"
    assert "springy bar" in page.text_content("#bench-idea")
    page.wait_for_function("document.querySelectorAll('#bench-headlines li').length === 2")
    assert "3 of 3 got out" in page.text_content("#bench-headlines")
    assert page.is_visible("#bench-uncalibrated")
    assert page.text_content("#bench-uncalibrated").startswith("UNCALIBRATED")
    assert "UNCALIBRATED" not in page.text_content("#bench-headlines")   # shown once, on its own line
    assert "`" not in page.text_content("#bench-headlines")   # `goal` is shown bold, without the backticks
    assert page.text_content("#bench-headlines strong") == "goal"
    assert re.search(r"the agent ran this .*ago", page.text_content("#bench-changed"))
    page.wait_for_function("document.querySelector('#bench-picture').src.startsWith('blob:')"
                           " && document.querySelector('#bench-picture').naturalWidth === 40")
    side = page.get_attribute("#bench-picture", "src")
    page.click("#bench-gif")
    page.wait_for_function("(s) => document.querySelector('#bench-picture').src !== s"
                           " && document.querySelector('#bench-picture').src.startsWith('blob:')", arg=side)
    assert page.get_attribute("#bench-gif", "aria-pressed") == "true"
    # the strip holds the other idea; a click puts it on stage (no run yet: no headlines, a plain hint)
    page.click("#bench-strip [data-file='pinch_car.toml']")
    _on_stage(page, "pinch car")
    assert page.is_visible("#bench-strip [data-file='bounce_bar.toml']")
    assert not page.query_selector("#bench-strip [data-file='pinch_car.toml']")
    assert page.query_selector_all("#bench-headlines li") == []
    assert page.is_visible("#bench-edit")


def test_change_it_edit_writes_file_and_moves_to_top(make_bench_page, tmp_path, units):
    units("metric")   # in meters and mm; test_a_length_shows_in_the_units_... has both systems
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    page.click("#bench-showall")
    box = "#bench-all input[data-key='bar.kicker.spring']"
    page.wait_for_selector(box)
    assert page.input_value(box) == "3000"
    page.fill(box, "4500")
    page.press(box, "Enter")
    f = tmp_path / "kickoff" / "bounce_bar.toml"
    deadline = time.time() + 5
    while "spring = 4500" not in f.read_text() and time.time() < deadline:
        time.sleep(0.1)
    assert "spring = 4500" in f.read_text()
    assert "# N/m, up and down" in f.read_text()   # the comment stays
    page.wait_for_function("document.querySelector('#bench-focus [data-field]')?.dataset.field === 'bar.kicker.spring'")
    assert page.input_value("#bench-focus input[data-key='bar.kicker.spring']") == "4500"
    assert "edited on this page" in page.text_content("#bench-changed")
    # help and unit come from the fields
    assert "N/m" in page.text_content("#bench-focus [data-field='bar.kicker.spring']")
    # a choice field is a list
    assert page.evaluate("document.querySelector(\"#bench-all select[data-key='wheel.top.tread']\").value") == "40A"
    # Ctrl+Z in bench mode undoes the idea on stage; never while the start page is shown
    page.click("#bench-name")
    page.click("#start-link")
    page.wait_for_selector("#start-page:not([hidden])")
    page.keyboard.press("Control+z")
    time.sleep(0.6)
    assert "spring = 4500" in f.read_text()
    page.click("#start-back")
    page.click("#bench-name")
    page.keyboard.press("Control+z")
    deadline = time.time() + 5
    while "spring = 3000" not in f.read_text() and time.time() < deadline:
        time.sleep(0.1)
    assert "spring = 3000" in f.read_text()


def test_agent_edit_appears_live(make_bench_page, tmp_path):
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    f = tmp_path / "kickoff" / "pinch_car.toml"
    text = f.read_text()
    head, tail = text.split("[shaft.top]", 1)
    f.write_text(head + "[shaft.top]" + tail.replace("target_rps = 40", "target_rps = 55", 1))
    _on_stage(page, "pinch car", timeout=2000)
    page.wait_for_function("document.querySelector('#bench-focus [data-field]')?.dataset.field === 'shaft.top.target_rps'",
                           timeout=2000)
    # the focus list (state) can be drawn a moment before the idea's values (its own fetch) arrive
    page.wait_for_function("document.querySelector(\"#bench-focus input[data-key='shaft.top.target_rps']\")?.value === '55'",
                           timeout=2000)
    assert "outside this page" in page.text_content("#bench-changed")


def test_agent_run_appears_live(make_bench_page, tmp_path):
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    folder = tmp_path / "kickoff"
    out = _fake_results(folder, "pinch_car", "pinch car (Maya)", ["3 of 3 left through `shot` at 6.9 m/s"])
    session.log_event(folder, "run", [folder / "pinch_car.toml"], out, "agent", True)
    _on_stage(page, "pinch car", timeout=2000)
    page.wait_for_function("document.querySelector('#bench-headlines').textContent.includes('6.9 m/s')", timeout=2000)
    assert page.text_content("#bench-uncalibrated").startswith("UNCALIBRATED")
    # the agent holding the run lock is said on the stage
    with session.run_lock(folder, {"source": "agent", "file": "bounce_bar.toml"}):
        page.wait_for_function("document.querySelector('#bench-lock') && !document.querySelector('#bench-lock').hidden"
                               " && document.querySelector('#bench-lock').textContent.includes('the agent is running bounce bar')")
    page.wait_for_function("document.querySelector('#bench-lock').hidden")
    # a run whose energy check failed is a failure in plain words, headlines and UNCALIBRATED still shown
    out = _fake_results(folder, "pinch_car", "pinch car (Maya)", ["the energy check failed: x", "3 of 3 out"], energy="pieces 0 gained energy")
    session.log_event(folder, "run", [folder / "pinch_car.toml"], out, "agent", True)
    page.wait_for_function("document.querySelector('#bench-failure') && !document.querySelector('#bench-failure').hidden"
                           " && document.querySelector('#bench-failure').textContent.includes('simulation')")
    assert "energy check failed" in page.text_content("#bench-headlines")
    assert page.is_visible("#bench-uncalibrated")
    # a run that failed says why
    session.log_event(folder, "run", [folder / "pinch_car.toml"], folder / "nowhere", "cli", False, "the inlet is inside the wheel")
    page.wait_for_function("document.querySelector('#bench-failure').textContent.includes('the inlet is inside the wheel')")
    assert page.is_visible("#bench-uncalibrated")
    # the idea on stage deleted outside: a notice, and the next idea on stage
    (folder / "pinch_car.toml").unlink()
    _on_stage(page, "bounce bar")
    page.wait_for_selector("#notices .notice:has-text('pinch_car.toml was deleted or renamed outside')")


def test_run_button_runs(make_bench_page, tmp_path):
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    page.click("#bench-strip [data-file='pinch_car.toml']")
    _on_stage(page, "pinch car")
    page.click("#bench-run")
    page.wait_for_function("document.querySelector('#bench-headlines').textContent.includes('left through')", timeout=90000)
    assert page.text_content("#bench-uncalibrated").startswith("UNCALIBRATED")
    assert "this page ran this" in page.text_content("#bench-changed")
    page.wait_for_function("document.querySelector('#bench-picture').src.startsWith('blob:')")
    assert (tmp_path / "kickoff" / "pinch_car-results" / "summary.txt").is_file()
    assert not page.is_visible("#bench-running")


def test_new_idea_from_example(make_bench_page, tmp_path):
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    page.click("#bench-new")
    page.fill("#bench-new-name", "Big wheel")
    page.fill("#bench-new-author", "Sam")
    page.fill("#bench-new-idea", "one big wheel flings it")
    page.select_option("#bench-new-from", "pinch_car")
    page.click("#bench-new-go")
    _on_stage(page, "Big wheel")
    assert page.text_content("#bench-author") == "Sam"
    f = tmp_path / "kickoff" / "big_wheel.toml"
    assert f.is_file() and 'author = "Sam"' in f.read_text() and "[[wheel]]" in f.read_text()
    assert page.is_visible("#bench-strip [data-file='bounce_bar.toml']")


def test_problem_marks_field(make_bench_page, tmp_path, units):
    units("metric")   # in meters and mm; test_a_length_shows_in_the_units_... has both systems
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    f = tmp_path / "kickoff" / "bounce_bar.toml"
    before = f.read_text()
    page.click("#bench-showall")
    box = "#bench-all input[data-key='wheel.top.diameter']"
    page.fill(box, "-0.05")
    page.press(box, "Enter")
    msg = "#bench-all [data-field='wheel.top.diameter'] .field-msg.error"
    page.wait_for_selector(msg)
    assert page.text_content(msg).strip()
    assert page.input_value(box) == "-0.05"   # what was typed stays, marked
    assert f.read_text() == before
    # a problem already in the file shows by its field and in the list under the panel
    f.write_text(before.replace("travel = 0.02", "travel = -0.02"))
    page.wait_for_selector("#bench-all [data-field='bar.kicker.travel'] .field-msg")
    page.wait_for_selector("#bench-problems li")
    assert "travel" in page.text_content("#bench-problems")


def test_typing_over_an_outside_edit_is_refused_not_saved(make_bench_page, tmp_path, units):
    units("metric")   # in meters and mm; test_a_length_shows_in_the_units_... has both systems
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    f = tmp_path / "kickoff" / "bounce_bar.toml"
    page.click("#bench-showall")
    box = "#bench-all input[data-key='bar.kicker.spring']"
    page.wait_for_selector(box)
    page.fill(box, "4500")                       # typed, not committed
    v = page.evaluate("__scrimmageBench.state.version")
    f.write_text(f.read_text().replace("spring = 3000.0", "spring = 5000.0"))   # the agent changes it meanwhile
    page.wait_for_function("(v) => __scrimmageBench.state.version > v && __scrimmageBench.idea.merged.bar[0].spring === 5000",
                           arg=v)
    assert page.input_value(box) == "4500"       # the half-typed text stays in its box
    page.press(box, "Enter")
    page.wait_for_selector("#notices .notice:has-text(\"wasn't saved\")")
    assert "spring = 5000.0" in f.read_text()
    page.wait_for_function("(s) => document.querySelector(s).value === '5000'", arg=box)
    # half-typed text when the stage moves to another idea: never written there, and said so
    page.fill(box, "4600")
    pinch = tmp_path / "kickoff" / "pinch_car.toml"
    pinch.write_text(pinch.read_text().replace("duration = 2.0", "duration = 2.5"))
    _on_stage(page, "pinch car")
    page.wait_for_selector("#notices .notice:has-text(\"bounce bar left the stage, so your edit to bar.kicker.spring wasn't saved\")")
    page.keyboard.press("Enter")
    time.sleep(0.5)
    assert "spring = 5000.0" in f.read_text() and "4600" not in pinch.read_text()


def test_half_typed_value_waits_for_enter_across_redraws(make_bench_page, tmp_path, units):
    units("metric")   # in meters and mm; test_a_length_shows_in_the_units_... has both systems
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    folder = tmp_path / "kickoff"
    f = folder / "bounce_bar.toml"
    before = f.read_text()
    page.click("#bench-showall")
    box = "#bench-all input[data-key='bar.kicker.spring']"
    page.wait_for_selector(box)
    page.click(box)
    page.keyboard.press("Control+a")
    page.keyboard.type("45")                     # half of 4500
    # unrelated version bumps (the run lock taken and given back) redraw the panel; nothing is committed
    v = page.evaluate("__scrimmageBench.state.version")
    with session.run_lock(folder, {"source": "agent", "file": "pinch_car.toml"}):
        page.wait_for_function("(v) => __scrimmageBench.state.version > v && !document.querySelector('#bench-lock').hidden", arg=v)
    page.wait_for_function("document.querySelector('#bench-lock').hidden")
    time.sleep(0.5)
    assert f.read_text() == before
    assert page.evaluate("document.activeElement.dataset.key") == "bar.kicker.spring"
    assert page.input_value(box) == "45"
    page.keyboard.type("00")
    page.keyboard.press("Enter")
    deadline = time.time() + 5
    while "spring = 4500" not in f.read_text() and time.time() < deadline:
        time.sleep(0.1)
    assert "spring = 4500" in f.read_text()


def _diameters(f: Path) -> dict:
    import tomllib
    return {w["name"]: {k: v for k, v in w.items() if k.startswith("diameter")} for w in tomllib.loads(f.read_text())["wheel"]}


def test_a_length_shows_in_the_units_and_is_written_as_the_file_writes_it(make_bench_page, tmp_path, units):
    """U3: a length is shown and typed in the Units setting; it is written in the form the file has, `diameter_in` in
    inches (Review Focus 2: typed in Metric) and `diameter` in meters, and a Units change writes nothing."""
    def before(folder):
        f = folder / "bounce_bar.toml"
        text = f.read_text()
        top = text.index('name = "top"')
        f.write_text(text[:top] + text[top:].replace("diameter = 0.1016", "diameter_in = 4.0", 1))
    units("metric")
    page = make_bench_page(before)
    _on_stage(page, "bounce bar")
    f = tmp_path / "kickoff" / "bounce_bar.toml"
    page.click("#bench-showall")
    top, bottom = ("#bench-all input[data-key='wheel.%s.diameter']" % w for w in ("top", "bottom"))
    page.wait_for_selector(top)
    assert page.input_value(top) == "101.6" and page.input_value(bottom) == "101.6"
    assert page.text_content("#bench-all [data-field='wheel.top.diameter'] .unit") == "mm"
    page.fill(top, "127")                       # 127 mm in a key the file writes in inches: 5 in
    page.press(top, "Enter")
    _wait_file(f, lambda t: _diameters(f)["top"] == {"diameter_in": 5.0})
    shows = lambda box, text: page.wait_for_function(   # noqa: E731 - the page has the file's new version
        "([s, t]) => { const b = document.querySelector(s); return b.value === t && b.dataset.version === "
        "String(__scrimmageBench.state.version); }", arg=[box, text])
    shows(top, "127")
    page.fill(bottom, "12 cm")                  # in meters, as the file writes it
    page.press(bottom, "Enter")
    _wait_file(f, lambda t: _diameters(f)["bottom"] == {"diameter": 0.12})
    shows(bottom, "120")
    page.fill(bottom, "5 in")                   # the other system's unit is refused, not converted
    page.press(bottom, "Enter")
    msg = "#bench-all [data-field='wheel.bottom.diameter'] .field-msg.error"
    page.wait_for_selector(msg)
    assert "Units is set to Metric, so type mm here, not in" in page.text_content(msg)
    page.fill(bottom, "110")
    page.press(bottom, "Enter")
    _wait_file(f, lambda t: _diameters(f)["bottom"] == {"diameter": 0.11})
    page.wait_for_function("(s) => !document.querySelector(s)", arg=msg)
    shows(bottom, "110")
    before_switch = f.read_text()
    page_units(page, "imperial")                # shown again in inches; nothing is written
    page.wait_for_function("(s) => document.querySelector(s).value === '5'", arg=top)
    assert page.input_value(bottom) == "4.33071"
    assert page.text_content("#bench-all [data-field='wheel.top.diameter'] .unit") == "in"
    page.press(top, "Enter")                    # unchanged: written back exactly
    page.wait_for_timeout(500)
    assert f.read_text() == before_switch
    page.fill(bottom, "4.5")
    page.press(bottom, "Enter")
    _wait_file(f, lambda t: _diameters(f)["bottom"] == {"diameter": 0.1143})
    assert _diameters(f)["top"] == {"diameter_in": 5.0}


def test_a_length_half_typed_when_units_change_is_converted(make_bench_page, tmp_path, units):
    """A6: "110" (mm) half typed when Units changes to Imperial comes back as 4.33071 in, and Enter writes 110 mm."""
    units("metric")
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    f = tmp_path / "kickoff" / "bounce_bar.toml"
    page.click("#bench-showall")
    bottom = "#bench-all input[data-key='wheel.bottom.diameter']"
    page.wait_for_selector(bottom)
    page.click(bottom)
    page.keyboard.press("Control+a")
    page.keyboard.type("110")                   # half typed: not committed
    page_units(page, "imperial")
    page.wait_for_function("document.querySelector(\"#bench-all [data-field='wheel.bottom.diameter'] .unit\")"
                           ".textContent === 'in'")
    assert page.input_value(bottom) == "4.33071"
    assert page.evaluate("document.activeElement.dataset.key") == "wheel.bottom.diameter"
    page.keyboard.press("Enter")
    _wait_file(f, lambda t: _diameters(f)["bottom"] == {"diameter": 0.11})


def test_a_value_typed_while_an_edit_is_on_its_way_is_saved(make_bench_page, tmp_path, units):
    """CI (a slow runner): the long poll brought an edit's new version, and redrew, before the edit's answer did. The
    box was stamped with the version the edit was typed at, so the next value typed in it was refused as stale by
    the page's own edit; a refusal made meanwhile was dropped by that answer; and a value typed in another box while
    an edit was on its way was refused as stale too."""
    units("metric")
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    f = tmp_path / "kickoff" / "bounce_bar.toml"
    page.evaluate("""() => { const f = window.fetch; window.fetch = async (u, o) => { const r = await f(u, o);
      if (String(u).includes('/api/bench/edit')) await new Promise(z => setTimeout(z, 800)); return r; }; }""")
    page.click("#bench-showall")
    top, bottom = ("#bench-all input[data-key='wheel.%s.diameter']" % w for w in ("top", "bottom"))
    page.wait_for_selector(bottom)
    v = page.evaluate("__scrimmageBench.state.version")
    page.fill(bottom, "12 cm")
    page.press(bottom, "Enter")
    page.wait_for_function("(v) => __scrimmageBench.state.version > v", arg=v)   # the poll's, before the answer
    page.fill(bottom, "5 in")                   # refused while the edit is on its way: its answer keeps the refusal
    page.press(bottom, "Enter")
    msg = "#bench-all [data-field='wheel.bottom.diameter'] .field-msg.error"
    page.wait_for_selector(msg)
    page.wait_for_timeout(1200)
    assert "Units is set to Metric" in page.text_content(msg, timeout=1000)
    assert page.input_value(bottom) == "5 in"
    page.fill(bottom, "110")
    page.press(bottom, "Enter")
    _wait_file(f, lambda t: _diameters(f)["bottom"] == {"diameter": 0.11})
    page.fill(top, "127")
    page.press(top, "Enter")
    page.fill(bottom, "115")                    # typed while the top's edit is on its way
    page.press(bottom, "Enter")
    _wait_file(f, lambda t: _diameters(f)["bottom"] == {"diameter": 0.115})
    assert _diameters(f)["top"] == {"diameter": 0.127}


def test_a_refusal_stays_until_its_own_field_commits(make_bench_page, tmp_path, units):
    """A7: edit A's server refusal stays, with its text, when a later edit B (another field) is saved; it goes when A
    commits again. A run's refusal goes when the idea changes outside the page (the edit that fixed it)."""
    units("metric")
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    f = tmp_path / "kickoff" / "bounce_bar.toml"
    page.click("#bench-showall")
    top, bottom = ("#bench-all input[data-key='wheel.%s.diameter']" % w for w in ("top", "bottom"))
    msg = "#bench-all [data-field='wheel.bottom.diameter'] .field-msg.error"
    page.wait_for_selector(bottom)
    page.fill(bottom, "-5")                     # refused by the server: the idea would have an error
    page.press(bottom, "Enter")
    page.wait_for_selector(msg)
    page.fill(top, "127")
    page.press(top, "Enter")
    _wait_file(f, lambda t: _diameters(f)["top"] == {"diameter": 0.127})
    page.wait_for_timeout(500)
    assert page.locator(msg).count() == 1      # B saved: A's refusal and its text stay
    assert page.input_value(bottom) == "-5"
    page.fill(bottom, "110")
    page.press(bottom, "Enter")
    _wait_file(f, lambda t: _diameters(f)["bottom"] == {"diameter": 0.11})
    page.wait_for_function("(s) => !document.querySelector(s)", arg=msg)

    # a run refused (the file has a mistake, made outside), then the mistake fixed outside: the refusal goes
    good = f.read_text()
    f.write_text(good.replace("diameter = 0.11", "diameter = -0.11", 1))
    page.wait_for_function("(s) => document.querySelector(s).value === '-110'", arg=bottom)
    page.click("#bench-run")
    page.wait_for_function("__scrimmageBench.bench.errors.run === true")
    f.write_text(good)
    page.wait_for_function("(s) => document.querySelector(s).value === '110'", arg=bottom)
    page.wait_for_function("!__scrimmageBench.bench.errors.list.length")


# -- the shape editor (Edit shape) ------------------------------------------------------------------------------------
def _wait_file(f: Path, pred, timeout=5.0) -> str:
    deadline = time.time() + timeout
    text = f.read_text()
    while not pred(text) and time.time() < deadline:
        time.sleep(0.1)
        text = f.read_text()
    assert pred(text), text
    return text


def _open_editor(page):
    page.click("#bench-edit")
    page.wait_for_selector("#bench-editor:not([hidden])")
    page.wait_for_function("__scrimmageBench.editor && __scrimmageBench.editor.instances.length > 0")
    assert not page.is_visible("#bench-stage")


def _selected(page) -> str:
    return page.evaluate("__scrimmageBench.editor.selected")


def _entry(raw_text: str, section: str, name: str) -> dict:
    import tomllib
    raw = tomllib.loads(raw_text)
    return next(e for e in raw[section] if e.get("name") == name)


def test_click_shape_selects_and_plus_moves_it(make_bench_page, tmp_path, units):
    units("metric")   # in meters and mm; test_a_length_shows_in_the_units_... has both systems
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    _open_editor(page)
    assert set(page.evaluate("__scrimmageBench.editor.instances")) >= {"bar.kicker", "wheel.top", "plate.feeder"}
    assert page.evaluate("__scrimmageBench.editor.boxes") == ["inlet.feed", "outlet.goal"]   # translucent boxes
    # the first view fits the shapes (not the bounds, which hold the 1 m outlet box): the kicker is big enough to hit
    page.wait_for_timeout(300)
    p = page.evaluate("__scrimmageBench.editor.screenPos('bar.kicker')")
    assert p, "the kicker is on screen"
    page.mouse.move(p["x"], p["y"])
    page.wait_for_function("__scrimmageBench.editor.hovered === 'bar.kicker'")   # hovering highlights it
    page.mouse.click(p["x"], p["y"])
    page.wait_for_function("__scrimmageBench.editor.selected === 'bar.kicker'")
    assert page.evaluate("__scrimmageBench.editor.outlined") == ["bar.kicker"]   # outlined in the accent color
    page.wait_for_selector("#bench-inspector [data-field='bar.kicker.spring']")
    assert page.text_content("#bench-inspector-title") == "kicker — bar"
    page.click("#bench-edit-zoom")   # zoom in on it: it stays under the middle of the view
    page.wait_for_timeout(300)
    assert page.evaluate("__scrimmageBench.editor.screenPos('bar.kicker')")
    # a bar given by its ends moves with its middle: + on the middle's z twice raises both ends 2 cm
    plus = "#bench-inspector [data-field='bar.kicker.center'] button[data-step='+'][data-axis='2']"
    v = page.evaluate("__scrimmageBench.state.version")
    page.click(plus)
    page.wait_for_function("(v) => __scrimmageBench.state.version > v", arg=v)
    page.click(plus)
    f = tmp_path / "kickoff" / "bounce_bar.toml"
    text = _wait_file(f, lambda t: _entry(t, "bar", "kicker")["b"][2] == pytest.approx(-0.038))
    bar = _entry(text, "bar", "kicker")
    assert bar["a"] == pytest.approx([0.25, -0.15, -0.038]) and bar["b"] == pytest.approx([0.25, 0.15, -0.038])
    assert "# N/m, up and down" in text
    # the view follows the edit
    page.wait_for_function("Math.abs(__scrimmageBench.editor.posOf('bar.kicker')[2] + 0.038) < 1e-6")
    # the end boxes show the new numbers, three boxes each
    assert page.input_value("#bench-inspector input[data-key='bar.kicker.a#2']") == "-38"   # mm
    # one edit moved both ends: one undo puts both back
    page.click("#undo")
    text = _wait_file(f, lambda t: _entry(t, "bar", "kicker")["b"][2] == pytest.approx(-0.048))
    assert _entry(text, "bar", "kicker")["a"][2] == pytest.approx(-0.048)
    page.click("#bench-edit-frame")   # Frame fits the bounds
    page.wait_for_timeout(200)
    # Back to the stage
    page.click("#bench-edit-back")
    page.wait_for_selector("#bench-stage:not([hidden])")
    assert not page.is_visible("#bench-editor")


def test_a_second_plus_counts_when_the_idea_reads_slowly(make_bench_page, tmp_path, units):
    """An edit bumps the session twice (the edit, then the talked-about list it writes): when the second bump's state
    comes while the edit's own refresh is still reading the idea, the next + still steps from the edited value."""
    units("metric")
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    _open_editor(page)
    page.wait_for_timeout(300)
    p = page.evaluate("__scrimmageBench.editor.screenPos('bar.kicker')")
    page.mouse.click(p["x"], p["y"])
    page.wait_for_function("__scrimmageBench.editor.selected === 'bar.kicker'")
    plus = "#bench-inspector [data-field='bar.kicker.center'] button[data-step='+'][data-axis='2']"
    page.wait_for_selector(plus)
    # each idea read answers 0.6 s late (a handler runs only while this test waits in a page call, not in _wait_file)
    page.route("**/api/bench/idea*", lambda r: (time.sleep(0.6), r.continue_()))
    page.click(plus)
    page.click(plus)
    page.wait_for_function("Math.abs(__scrimmageBench.editor.posOf('bar.kicker')[2] + 0.038) < 1e-6")
    page.unroute("**/api/bench/idea*")
    bar = _entry((tmp_path / "kickoff" / "bounce_bar.toml").read_text(), "bar", "kicker")
    assert bar["a"][2] == pytest.approx(-0.038) and bar["b"][2] == pytest.approx(-0.038)


def test_plus_nudges_a_radius(make_bench_page, tmp_path, units):
    """A length in meters (a radius here) has - and + too: 10 mm a click. The title names the shape once."""
    units("metric")   # in meters and mm; test_a_length_shows_in_the_units_... has both systems
    def before(folder):
        shutil.copy(REPO / "examples" / "bench" / "hooded.toml", folder / "hooded.toml")
    page = make_bench_page(before)
    _on_stage(page, "hooded")
    _open_editor(page)
    page.evaluate("__scrimmageBench.select('curve.hood')")
    page.wait_for_selector("#bench-inspector [data-field='curve.hood.radius']")
    assert page.text_content("#bench-inspector-title") == "hood — curve"
    plus = "#bench-inspector [data-field='curve.hood.radius'] button[data-step='+']"
    assert "10 mm" in page.get_attribute(plus, "title")
    page.click(plus)
    f = tmp_path / "kickoff" / "hooded.toml"
    _wait_file(f, lambda t: _entry(t, "curve", "hood")["radius"] == pytest.approx(0.2005))
    page.wait_for_function("document.querySelector(\"#bench-inspector input[data-key='curve.hood.radius#0']\")?.value === '200.5'")
    page.click("#bench-inspector [data-field='curve.hood.radius'] button[data-step='-']")
    _wait_file(f, lambda t: _entry(t, "curve", "hood")["radius"] == pytest.approx(0.1905))
    # a wheel's diameter too
    page.evaluate("__scrimmageBench.select('wheel.wheel')")
    page.wait_for_selector("#bench-inspector [data-field='wheel.wheel.diameter'] button[data-step='+']")


# The add / delete tests make many round trips one after another (each a write, a long poll and a redraw); under a
# full parallel run each can take seconds, so they wait longer than the page's default and are split three ways.
SLOW_MS, SLOW_S = 60000, 30.0


def _editing_bounce_bar(make_bench_page, tmp_path):
    page = make_bench_page()
    page.set_default_timeout(SLOW_MS)
    _on_stage(page, "bounce bar", timeout=SLOW_MS)
    _open_editor(page)
    return page, tmp_path / "kickoff" / "bounce_bar.toml"


def test_add_wheel_then_delete(make_bench_page, tmp_path):
    page, f = _editing_bounce_bar(make_bench_page, tmp_path)
    page.click("#bench-add [data-add='wheel']")
    page.wait_for_function("__scrimmageBench.editor.selected === 'wheel.wheel1'")
    assert 'name = "wheel1"' in _wait_file(f, lambda t: 'name = "wheel1"' in t, SLOW_S)
    page.wait_for_function("__scrimmageBench.editor.instances.includes('wheel.wheel1')")
    page.wait_for_selector("#bench-inspector [data-field='wheel.wheel1.diameter']")
    page.click("#bench-delete")
    _wait_file(f, lambda t: 'name = "wheel1"' not in t, SLOW_S)
    page.wait_for_function("!__scrimmageBench.editor.instances.includes('wheel.wheel1')")
    assert _selected(page) is None


def test_shaft_with_a_wheel_cant_be_deleted(make_bench_page, tmp_path):
    # a shaft a wheel is on can't be deleted: the server's message, nothing written
    page, f = _editing_bounce_bar(make_bench_page, tmp_path)
    before = f.read_text()
    page.click("#bench-entries [data-entry='shaft.top']")
    page.wait_for_function("__scrimmageBench.editor.selected === 'shaft.top'")
    page.click("#bench-delete")
    page.wait_for_selector("#bench-inspector .field-msg.error:has-text(\"is used by wheel 'top'\")")
    assert f.read_text() == before


def test_other_add_buttons_select_what_they_add(make_bench_page, tmp_path):
    page, f = _editing_bounce_bar(make_bench_page, tmp_path)
    for section in ("plate", "bar", "curve", "inlet", "outlet", "shaft"):
        page.click(f"#bench-add [data-add='{section}']")
        page.wait_for_function("(s) => __scrimmageBench.editor.selected === s", arg=f"{section}.{section}1")
        page.wait_for_selector(f"#bench-entries [data-entry='{section}.{section}1'][aria-pressed='true']")
    text = _wait_file(f, lambda t: '[shaft.shaft1]' in t and 'name = "curve1"' in t, SLOW_S)
    assert '[shaft.shaft1]' in text and 'name = "curve1"' in text


def test_add_cad_part_asks_for_a_file(make_bench_page, tmp_path):
    def before(folder):
        from test_bench_cad import cube, write_stl
        write_stl(folder / "block.stl", *cube(0.0, 20.0))   # a 20 mm cube
    page = make_bench_page(before)
    _on_stage(page, "bounce bar")
    _open_editor(page)
    page.click("#bench-add [data-add='cad']")
    page.wait_for_selector("#bench-cad-dialog[open]")
    page.wait_for_selector("#bench-cad-browse [data-entry]")
    page.fill("#bench-cad-mesh", "block.stl")
    page.select_option("#bench-cad-units", "mm")
    page.click("#bench-cad-go")
    page.wait_for_function("__scrimmageBench.editor.selected === 'cad.cad1'")
    text = _wait_file(tmp_path / "kickoff" / "bounce_bar.toml", lambda t: "[[cad]]" in t)
    assert 'mesh = "block.stl"' in text and 'units = "mm"' in text


def test_included_entry_read_only_then_override(make_bench_page, tmp_path):
    def before(folder):
        text = (folder / "bounce_bar.toml").read_text()
        head, tail = text.split("# 4 in.")
        (folder / "shafts.toml").write_text("[piece]" + head.split("[piece]")[1])
        own = 'include = ["shafts.toml"]\n\n[bench]\nname = "split"\nauthor = "Kai"\nidea = "shared shafts"\n\n# 4 in.'
        (folder / "split.toml").write_text(own + tail)
    page = make_bench_page(before)
    _on_stage(page, "split")
    _open_editor(page)
    page.click("#bench-entries [data-entry='shaft.top']")
    page.wait_for_function("__scrimmageBench.editor.selected === 'shaft.top'")
    assert "from shafts.toml" in page.text_content("#bench-inspector .bench-from")
    assert page.is_disabled("#bench-inspector input[data-key='shaft.top.target_rps']")
    assert not page.query_selector("#bench-delete:not([disabled])")
    page.click("#bench-override")
    f = tmp_path / "kickoff" / "split.toml"
    _wait_file(f, lambda t: "[shaft.top]" in t)
    box = "#bench-inspector input[data-key='shaft.top.target_rps']"
    page.wait_for_selector(box + ":not([disabled])")
    assert not page.query_selector("#bench-inspector .bench-from")
    page.fill(box, "50")
    page.press(box, "Enter")
    _wait_file(f, lambda t: "target_rps = 50" in t)
    assert "target_rps = 30" in (tmp_path / "kickoff" / "shafts.toml").read_text()


def test_undo_redo_in_editor(make_bench_page, tmp_path, units):
    units("metric")   # in meters and mm; test_a_length_shows_in_the_units_... has both systems
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    _open_editor(page)
    page.evaluate("__scrimmageBench.select('plate.feeder')")
    plus = "#bench-inspector [data-field='plate.feeder.center'] button[data-step='+'][data-axis='0']"
    page.click(plus)
    f = tmp_path / "kickoff" / "bounce_bar.toml"
    x = lambda t: _entry(t, "plate", "feeder")["center"][0]   # noqa: E731
    _wait_file(f, lambda t: x(t) == pytest.approx(-0.21))
    assert "center = [-0.21, 0.0, -0.08]" in f.read_text()   # rounded, not -0.21000000000000002
    page.wait_for_selector("#undo:not([disabled])")
    page.click("#undo")
    _wait_file(f, lambda t: x(t) == pytest.approx(-0.22))
    page.wait_for_function("(s) => document.querySelector(s).value === '-220'",   # mm
                           arg="#bench-inspector input[data-key='plate.feeder.center#0']")
    page.click("#redo")
    _wait_file(f, lambda t: x(t) == pytest.approx(-0.21))
    page.wait_for_selector("#undo:not([disabled])")
    page.click("#bench-inspector-title")
    page.keyboard.press("Control+z")
    _wait_file(f, lambda t: x(t) == pytest.approx(-0.22))
    assert _selected(page) == "plate.feeder"
    # an agent edit to another idea doesn't take the editor off the idea being edited
    pinch = tmp_path / "kickoff" / "pinch_car.toml"
    pinch.write_text(pinch.read_text().replace("duration = 2.0", "duration = 2.5"))
    page.wait_for_function("__scrimmageBench.state.stage === 'pinch_car.toml'")
    page.wait_for_timeout(300)
    assert page.evaluate("__scrimmageBench.shown") == "bounce_bar.toml" and _selected(page) == "plate.feeder"


def test_help_text_shown_for_field(make_bench_page, tmp_path, units):
    units("metric")   # in meters and mm; test_a_length_shows_in_the_units_... has both systems
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    _open_editor(page)
    page.evaluate("__scrimmageBench.select('bar.kicker')")
    row = "#bench-inspector [data-field='bar.kicker.spring']"
    page.wait_for_selector(row)
    text = page.text_content(row)
    assert "makes it springy along spring_axis" in text and "N/m" in text
    assert page.evaluate("document.querySelector(\"#bench-inspector select[data-key='bar.kicker.material']\").value") == "aluminum"
    # a problem in the file marks its field in the inspector; a half-typed new name survives that redraw
    page.fill("#bench-rename-name", "flipper")
    f = tmp_path / "kickoff" / "bounce_bar.toml"
    f.write_text(f.read_text().replace("travel = 0.02", "travel = -0.02"))
    page.wait_for_selector("#bench-inspector [data-field='bar.kicker.travel'] .field-msg")
    assert page.input_value("#bench-rename-name") == "flipper"
    # rename through the inspector
    page.click("#bench-rename")
    page.wait_for_function("__scrimmageBench.editor.selected === 'bar.flipper'")
    _wait_file(f, lambda t: 'name = "flipper"' in t)
    page.wait_for_selector("#bench-entries [data-entry='bar.flipper'][aria-pressed='true']")
    assert page.text_content("#bench-inspector-title") == "flipper — bar"


def test_typing_in_the_inspector_over_an_outside_edit_is_refused(make_bench_page, tmp_path, units):
    units("metric")   # in meters and mm; test_a_length_shows_in_the_units_... has both systems
    page = make_bench_page()
    _on_stage(page, "bounce bar")
    _open_editor(page)
    page.evaluate("__scrimmageBench.select('plate.feeder')")
    box = "#bench-inspector input[data-key='plate.feeder.center#0']"
    page.wait_for_selector(box)
    page.click(box)
    page.keyboard.press("Control+a")
    page.keyboard.type("-0.3")                   # typed, not committed
    f = tmp_path / "kickoff" / "bounce_bar.toml"
    v = page.evaluate("__scrimmageBench.state.version")
    f.write_text(f.read_text().replace("spring = 3000.0", "spring = 5000.0"))   # the agent changes the idea meanwhile
    page.wait_for_function("(v) => __scrimmageBench.state.version > v && __scrimmageBench.idea.merged.bar[0].spring === 5000",
                           arg=v)
    page.wait_for_timeout(300)
    assert page.input_value(box) == "-0.3"       # the half-typed text survives the redraw
    assert page.evaluate("document.activeElement.dataset.key") == "plate.feeder.center#0"
    page.keyboard.press("Enter")
    page.wait_for_selector("#notices .notice:has-text(\"your edit to plate.feeder.center wasn't saved\")")
    page.wait_for_function("(s) => document.querySelector(s).value === '-220'", arg=box)   # mm
    text = f.read_text()
    assert "spring = 5000.0" in text and "center = [-0.22, 0.0, -0.08]" in text


def _png(path: Path, w: int, color=(30, 160, 60)) -> None:
    from PIL import Image
    Image.new("RGB", (w, 20), color).save(path)


def test_agent_sweep_and_compare_show_on_stage(make_bench_page, tmp_path):
    """A sweep or compare made by the agent (or the CLI) shows on the stage with its chart, its table and its
    UNCALIBRATED line; each idea keeps its own run, shown when it is clicked; a reload keeps it all."""
    def before(folder):
        out = _fake_results(folder, "bounce_bar", "bounce bar (Priya)", ["3 of 3 got out through `goal`"])
        session.log_event(folder, "run", [folder / "bounce_bar.toml"], out, "agent", True)
    page = make_bench_page(before)
    folder = tmp_path / "kickoff"
    _on_stage(page, "bounce bar")
    page.wait_for_function("document.querySelector('#bench-picture').naturalWidth === 40")
    # the agent sweeps into the run's own results folder (bench_sweep's default)
    out = folder / "bounce_bar-results"
    (out / "results.csv").write_text("bar.kicker.spring,out,out_other,jammed,exit_speed,peak_load_n\n"
                                     "2000,3,0,0,7.5,40\n4000,2,0,1,6.25,52\n")
    _png(out / "line-exit_speed-vs-bar.kicker.spring.png", 60)
    session.log_event(folder, "sweep", [folder / "bounce_bar.toml"], out, "agent", True)
    _on_stage(page, "bounce bar, swept", timeout=5000)
    page.wait_for_function("document.querySelector('#bench-picture').naturalWidth === 60")   # the chart, not the run
    assert page.query_selector_all("#bench-headlines li") == []   # never the run's headlines
    assert page.is_visible("#bench-table")
    # the swept key's values follow the Units setting too, as From and To do (cleanup A4): 2000 N/m is 11.42 lbf/in
    assert page.text_content("#bench-table th") == "bar.kicker.spring (lbf/in)"
    assert len(page.query_selector_all("#bench-table tbody tr")) == 2
    # the CSV is SI; the table is in the Units setting (Imperial): 7.5 m/s is 24.61 ft/s, 40 N is 8.992 lbf
    assert "exit speed (ft/s)" in page.text_content("#bench-table thead")
    assert "peak load (lbf)" in page.text_content("#bench-table thead")
    first = page.eval_on_selector_all("#bench-table tbody tr:first-child td", "ts => ts.map(t => t.textContent)")
    assert first == ["11.42", "3", "0", "24.61", "8.992"]
    page_units(page, "metric")
    page.wait_for_function("document.querySelector('#bench-table thead').textContent.includes('exit speed (m/s)')")
    first = page.eval_on_selector_all("#bench-table tbody tr:first-child td", "ts => ts.map(t => t.textContent)")
    assert first == ["2000", "3", "0", "7.5", "40"]
    assert page.text_content("#bench-table th") == "bar.kicker.spring (N/m)"
    assert page.text_content("#bench-uncalibrated").startswith("UNCALIBRATED")
    page.wait_for_timeout(1200)   # the "... ago" line is kept current: it still names the sweep
    assert re.search(r"the agent swept this .*ago", page.text_content("#bench-changed"))
    assert not page.is_visible("#bench-gif")
    # the agent compares two ideas: one picture and a table, side by side
    cmp = folder / "compare-results"
    cmp.mkdir()
    (cmp / "compare.csv").write_text("idea,out,jammed,exit_speed\nbounce bar,3,0,9.5\npinch car,2,1,6\n")
    _png(cmp / "compare.png", 80)
    session.log_event(folder, "compare", [folder / "bounce_bar.toml", folder / "pinch_car.toml"], cmp, "agent", True)
    _on_stage(page, "Comparing bounce bar and pinch car", timeout=5000)
    page.wait_for_function("document.querySelector('#bench-picture').naturalWidth === 80")
    rows = page.eval_on_selector_all("#bench-table tbody tr", "rs => rs.map(r => r.cells[0].textContent)")
    assert rows == ["bounce bar", "pinch car"]
    assert page.is_visible("#bench-uncalibrated")
    assert page.text_content("#bench-author") == "Priya and Maya"
    # Run, Edit shape and Change it act on one idea: while a compare is on stage they say which, on the buttons
    assert page.text_content("#bench-run").strip() == "Run bounce bar"
    assert page.text_content("#bench-change-run").strip() == "Run bounce bar"
    assert page.text_content("#bench-change-title").strip() == "Change it: bounce bar"
    # every idea is in the strip; a click shows that idea's own run, its headlines kept through the compare
    page.click("#bench-strip [data-file='bounce_bar.toml']")
    _on_stage(page, "bounce bar")
    assert page.text_content("#bench-run").strip() == "Run"
    assert page.text_content("#bench-change-title").strip() == "Change it"
    page.wait_for_function("document.querySelector('#bench-headlines').textContent.includes('3 of 3 got out')")
    page.wait_for_function("document.querySelector('#bench-picture').naturalWidth === 40")
    assert not page.is_visible("#bench-table")
    assert page.is_visible("#bench-uncalibrated")
    # the result's card puts it back
    page.click("#bench-strip [data-result='compare']")
    _on_stage(page, "Comparing bounce bar and pinch car")
    # a reload shows it too (the server says it is on stage)
    page.reload()
    page.wait_for_function("window.__scrimmageBench && __scrimmageBench.state && __scrimmageBench.shown")
    _on_stage(page, "Comparing bounce bar and pinch car", timeout=5000)
    page.wait_for_function("document.querySelector('#bench-picture').naturalWidth === 80")
    # an edit moves the stage to that idea's own result
    f = folder / "pinch_car.toml"
    f.write_text(f.read_text().replace('idea = "', 'idea = "Now ', 1))
    _on_stage(page, "pinch car", timeout=5000)
    assert not page.is_visible("#bench-table")


@pytest.mark.skipif(not os.environ.get("SCRIMMAGE_BENCH_SHOT"), reason="set SCRIMMAGE_BENCH_SHOT=<png> to write docs/img/bench-tab.png")
def test_bench_tab_screenshot(make_bench_page):
    """The Stage after a real run, at a projector size, for docs/bench-tab.md."""
    page = make_bench_page()
    page.set_viewport_size({"width": 1920, "height": 1080})
    _on_stage(page, "bounce bar")
    page.click("#bench-run")
    page.wait_for_function("document.querySelector('#bench-headlines').textContent.includes('left through')", timeout=90000)
    page.wait_for_function("document.querySelector('#bench-picture').naturalWidth > 100")
    page.wait_for_timeout(800)
    page.screenshot(path=os.environ["SCRIMMAGE_BENCH_SHOT"])
