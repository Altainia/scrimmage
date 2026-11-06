"""Browser end-to-end tests of the Scenario tab of `scrimmage edit` (headless Chromium through Playwright).

The fixtures (Chromium lookup, SCRIMMAGE_REQUIRE_BROWSER, the server, `scenario_page`) live in
tests/test_assembler_browser.py, which also holds the field-picker, fail-fix-pass and Stop tests. This file adds the
rest of the spec's list: add a step, set its mode, duration and an axis, add an `at_end` check from the signal
picker, switch to the Robot tab; and the opt-in README screenshot.

Set SCRIMMAGE_README_SHOT=<file.png> to also run the test that writes the README screenshot (docs/img/scenario-editor.png).
"""
import os
import shutil
import time

import pytest

from test_assembler_browser import REPO, Server, _run, browser, scenario_page  # noqa: F401  (fixtures)

pytestmark = pytest.mark.browser


def _version(page) -> int:
    return page.evaluate("__scrimmage.scenario.state.version")


def _wait_edit(page, v: int):
    page.wait_for_function(f"__scrimmage.scenario.state.version > {v}")


def test_add_step_set_mode_duration_axis_and_signal_check(scenario_page):
    page, scn = scenario_page("fake_pass.toml")
    page.click("#step-row-1")

    # add a step after the selected one; it becomes the selected step
    v = _version(page)
    page.click("[data-key='tl-add']")
    page.wait_for_selector("#step-row-2")
    _wait_edit(page, v)
    assert page.locator(".step-row").count() == 3
    page.wait_for_selector("#step-row-2.selected")

    # mode and duration
    v = _version(page)
    page.click("[data-key='mode-test']")
    _wait_edit(page, v)
    assert page.evaluate("__scrimmage.scenario.state.steps[2].mode") == "test"
    v = _version(page)
    page.fill("#ed-duration", "2.5")
    page.keyboard.press("Enter")
    _wait_edit(page, v)
    assert page.evaluate("__scrimmage.scenario.state.steps[2].duration") == 2.5

    # an axis: add port 0, add an axis, set it to 0.5
    page.select_option("#ed-add-port", "0")
    page.wait_for_selector("[data-port='0']")
    v = _version(page)
    page.click("[aria-label='Add axis on port 0']")
    _wait_edit(page, v)
    page.wait_for_selector("#ax-0-0")
    v = _version(page)
    page.fill("#ax-0-0", "0.5")
    page.keyboard.press("Enter")
    _wait_edit(page, v)
    assert page.evaluate("__scrimmage.scenario.state.steps[2].axes['0']") == [0.5]

    # an at_end check, its signal chosen in the signal picker
    v = _version(page)
    page.select_option("#ed-add-check", "at_end")
    _wait_edit(page, v)
    page.select_option("#ck-2-0-signal-pick", "motor.left_drive.current")
    page.wait_for_function("__scrimmage.scenario.state.steps[2].checks[0].spec.signal === 'motor.left_drive.current'")
    spec = page.evaluate("__scrimmage.scenario.state.steps[2].checks[0].spec")
    assert spec["signal"] == "motor.left_drive.current"

    text = scn.read_text()
    assert 'mode = "test"' in text and "duration = 2.5" in text and "0.5" in text
    assert "motor.left_drive.current" in text

    # the edited scenario still runs and passes (the new step has no failing check)
    page.fill("#ck-2-0-max", "1000")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.scenario.state.steps[2].checks[0].spec.max === 1000")
    page.fill("#ck-2-0-min", "")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.scenario.state.steps[2].checks[0].spec.min === undefined")
    _run(page, "passed")
    page.wait_for_selector("#step-row-2 .run-dot.run-pass")


def test_robot_tab_opens_and_switches_back(scenario_page):
    page, _ = scenario_page("fake_pass.toml")
    page.click("#tab-btn-robot")
    page.wait_for_selector("#tab-robot:not([hidden])")
    assert page.is_hidden("#tab-scenario")
    page.wait_for_function("__scrimmage.state && __scrimmage.state.parts !== undefined")
    page.click("#tab-btn-scenario")
    page.wait_for_selector("#tab-scenario:not([hidden])")
    page.wait_for_selector(".step-row")


def test_first_robot_edit_refreshes_the_signal_picker(browser, tmp_path):
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    shutil.copytree(REPO / "scenarios", tmp_path / "scenarios")
    srv = Server(tmp_path / "scenarios" / "kitbot_auto.toml", cwd=tmp_path)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    logs = []
    try:
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        page.goto(srv.url)
        page.wait_for_selector(".step-row")
        page.wait_for_function("__scrimmage.scenario && __scrimmage.scenario.signals && __scrimmage.scenario.signals.motors")
        assert "motor.dt.left" in page.evaluate("__scrimmage.scenario.signals.motors")
        # rename the drivetrain on the Robot tab: the first robot edit of the session
        page.click("#tab-btn-robot")
        page.wait_for_selector("#tab-robot:not([hidden])")
        page.click('#tree [role="treeitem"][data-name="dt"] .tree-row')
        page.wait_for_selector("#insp-name")
        v = page.evaluate("__scrimmage.state.version")
        page.fill("#insp-name", "base")
        page.keyboard.press("Enter")
        page.wait_for_function(f"__scrimmage.state.version > {v} && __scrimmage.state.parts.some(p => p.name === 'base')")
        page.click("#tab-btn-scenario")
        page.wait_for_selector("#tab-scenario:not([hidden])")
        page.wait_for_function("__scrimmage.scenario.signals.motors.includes('motor.base.left')", timeout=10000)
        # the signal picker of the battery check lists the new name
        page.click("#step-row-1")
        page.wait_for_selector("#ck-1-1-signal-pick")
        opts = page.eval_on_selector_all("#ck-1-1-signal-pick option", "os => os.map(o => o.value)")
        assert "motor.base.left.current" in opts and not any(o.startswith("motor.dt.") for o in opts), opts
    finally:
        ctx.close()
        srv.stop()
    assert not logs, logs


def test_stop_is_enabled_and_works_while_the_robot_program_starts(scenario_page, tmp_path):
    from test_scenario_runs import _marked_pids, slow_robot_scenario
    page, scn = scenario_page("fake_pass.toml")
    slow, _, marker = slow_robot_scenario(tmp_path)   # writes tmp_path/slow_robot.toml (never connects)
    v = page.evaluate("__scrimmage.scenario.state.version")
    scn.write_text(scn.read_text().replace(f'"{REPO / "tests" / "fake_robot.toml"}"', '"slow_robot.toml"'))
    page.wait_for_function(f"__scrimmage.scenario.state.version > {v} && "
                           "__scrimmage.scenario.state.top.config === 'slow_robot.toml' && "
                           "__scrimmage.scenario.state.errors.length === 0")
    page.click("#run-btn")
    page.wait_for_function("__scrimmage.scenario.runs.length === 1 && __scrimmage.scenario.runs[0].state === 'running'")
    page.wait_for_function("document.querySelector('#run-step') && "
                           "document.querySelector('#run-step').textContent.includes('starting the robot program')")
    deadline = time.monotonic() + 30
    while not _marked_pids(marker):
        assert time.monotonic() < deadline, "the robot program didn't start"
        time.sleep(0.05)
    assert page.is_enabled("#run-stop")
    t0 = time.monotonic()
    page.click("#run-stop")
    page.wait_for_function("__scrimmage.scenario.runs[0].state !== 'running'", timeout=10000)
    assert time.monotonic() - t0 < 3
    assert page.evaluate("__scrimmage.scenario.runs[0].state") == "stopped"
    assert _marked_pids(marker) == []


@pytest.mark.skipif(not os.environ.get("SCRIMMAGE_README_SHOT"),
                    reason="set SCRIMMAGE_README_SHOT=<png> to write the README screenshot")
def test_readme_screenshot(scenario_page, tmp_path):
    # the server runs in tmp_path with the scenario there, so the header shows just "fake_fail.toml"
    page, _ = scenario_page("fake_fail.toml")
    assert str(tmp_path) not in page.inner_text("header")
    page.click("#step-row-1")
    _run(page, "failed")
    page.wait_for_selector("#run-report .rp-check.rp-fail")
    # the page shows absolute paths (the robot config, the report folder): show neutral ones in the picture
    page.evaluate("""([map]) => {
        const swap = (t) => map.reduce((a, [from, to]) => a.split(from).join(to), t);
        const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        for (let n = w.nextNode(); n; n = w.nextNode()) n.nodeValue = swap(n.nodeValue);
        for (const el of document.querySelectorAll("input, [title]")) {
            if (el.value) el.value = swap(el.value);
            if (el.title) el.title = swap(el.title);
        }
    }""", [[[str(tmp_path), "/home/me/my-robot"], [str(REPO / "tests"), "/home/me/my-robot/robots"]]])
    shown = page.inner_text("body")
    assert str(tmp_path) not in shown and str(REPO) not in shown and "/home/" + os.environ.get("USER", "-") not in shown
    page.wait_for_timeout(300)
    page.screenshot(path=os.environ["SCRIMMAGE_README_SHOT"])


def test_report_shows_ball_table(browser, tmp_path):
    """A run on the test range: the report lists the score and one row per ball (the spare slots aside) with its
    outcome."""
    from range_fixtures import shooter_cfg
    from test_range_checks import write_config
    from scrimmage.testrange.spec import load_range
    cfg = write_config(tmp_path, shooter_cfg(range={"preload": 2, "start": "blue_center"}))   # the page asks for one
    scn = tmp_path / "range.toml"
    scn.write_text(f'config = "{cfg.name}"\n\n[range]\npreload = 3\n\n[[step]]\nmode = "disabled"\nduration = 0.2\n'
                   '  [[step.check]]\n  at_end = { signal = "held", equals = 3 }\n')
    srv = Server(scn, cwd=tmp_path)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    logs = []
    try:
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        page.goto(srv.url)
        page.wait_for_selector(".step-row")
        page.wait_for_function("__scrimmage.scenario && __scrimmage.scenario.signals && __scrimmage.scenario.runs")
        assert "score.hub_blue" in page.evaluate("__scrimmage.scenario.signals.range")
        _run(page, "passed")
        page.wait_for_selector("#rp-balls")
        rows = page.eval_on_selector_all("#rp-balls tbody tr", "rs => rs.map(r => r.querySelector('.rp-outcome').textContent)")
        assert rows == ["held", "held", "held"], rows
        # positions and speeds in the chosen units (Imperial, the default), and at once in the other
        head = page.inner_text("#rp-balls thead")
        assert "x ft" in head and "vx ft/s" in head and "exit ft/s" in head
        from browser_harness import page_units
        page_units(page, "metric")
        page.wait_for_function("document.querySelector('#rp-balls thead').textContent.includes('x m')")
        assert "vx m/s" in page.inner_text("#rp-balls thead")
        score = page.inner_text("#rp-score")
        assert "hub_blue: 0" in score and "held: 3" in score and "fired: 0" in score
        # the floor balls are off (not in the world), and the robot holds 4: one slot is spare, and the OUTPOSTS'
        # FUEL wait parked
        parked = sum(s.pool() for s in load_range("2026", REPO).stations)
        assert (f"Not listed: {parked + 1} spare balls (unused slots, or floor balls turned off)."
                in page.inner_text("#rp-balls .rp-not-listed"))
    finally:
        ctx.close()
        srv.stop()
    assert not logs, logs


def test_match_station_and_a_steps_game_data_write_the_file(scenario_page):
    page, scn = scenario_page("fake_pass.toml")
    v = _version(page)
    page.select_option("#sc-station", "red1")
    _wait_edit(page, v)
    assert page.evaluate("__scrimmage.scenario.state.top.match") == {"station": "red1"}
    assert '[match]\nstation = "red1"\n' in scn.read_text()

    page.click("#step-row-1")
    page.wait_for_selector("#step-row-1.selected")
    v = _version(page)
    page.fill("#ed-game-data", "B")
    page.keyboard.press("Enter")
    _wait_edit(page, v)
    assert page.evaluate("__scrimmage.scenario.state.steps[1].game_data") == "B"
    assert 'duration = 1.0\ngame_data = "B"\n' in scn.read_text()

    # step 1's box says it has none; a bad length is refused beside the Match row
    page.click("#step-row-0")
    page.wait_for_selector("#ed-game-data[placeholder='none']")
    before = scn.read_text()
    page.fill("#sc-auto_seconds", "-1")
    page.keyboard.press("Enter")
    page.wait_for_selector(".sc-match .field-msgs .error")
    assert "auto_seconds must be a positive number" in page.inner_text(".sc-match .field-msgs")
    assert scn.read_text() == before


def test_a_pose_check_shows_and_takes_the_chosen_units(scenario_page):
    """Units (Settings): a pose check's x, y and tol_xy are field lengths, feet (the default) or meters; the file keeps
    meters, a typed x reads back as typed (Review Focus 1), and switching writes nothing."""
    from browser_harness import page_units
    page, scn = scenario_page("fake_pass.toml")
    page.click("#step-row-1")
    v = _version(page)
    page.select_option("#ed-add-check", "pose")
    _wait_edit(page, v)
    j = page.evaluate("__scrimmage.scenario.state.steps[1].checks.length - 1")
    spec = lambda: page.evaluate(f"__scrimmage.scenario.state.steps[1].checks[{j}].spec")  # noqa: E731
    x, y, tol = f"#ck-1-{j}-x", f"#ck-1-{j}-y", f"#ck-1-{j}-tol_xy"
    page.wait_for_selector(x)
    assert page.inner_text(f'[data-fields="step1/check{j}/pose.x"] .unit') == "ft"
    assert page.get_attribute(tol, "placeholder") == "0.33"            # 0.1 m
    v = _version(page)
    page.fill(x, "12.5")
    page.keyboard.press("Enter")
    _wait_edit(page, v)
    assert spec()["x"] == 3.81 and spec()["y"] == 0
    assert "x = 3.81" in scn.read_text()
    assert page.input_value(x) == "12.5"                               # reads back as typed
    v = _version(page)
    page.fill(tol, "15 cm")                                            # the other system's length: refused
    page.keyboard.press("Enter")
    page.wait_for_selector(f'[data-fields="step1/check{j}/pose.tol_xy"] .error')
    assert "Units is set to Imperial" in page.inner_text(f'[data-fields="step1/check{j}/pose.tol_xy"] .error')
    page.fill(tol, "6 in")                                             # any length of the system: 6 in where ft shows
    page.keyboard.press("Enter")
    _wait_edit(page, v)
    assert spec()["tol_xy"] == pytest.approx(0.1524)
    saved = scn.read_text()

    page.fill(y, "2")                                                  # half typed when the setting changes
    page.focus(y)
    page_units(page, "metric")
    page.wait_for_function(f"document.querySelector('{x}').value === '3.81'")
    assert page.inner_text(f'[data-fields="step1/check{j}/pose.x"] .unit') == "m"
    assert page.input_value(y) == "0.6096" and page.evaluate("document.activeElement.id") == f"ck-1-{j}-y"   # 2 ft (A6)
    assert scn.read_text() == saved
    v = _version(page)
    page.keyboard.press("Enter")                                       # the 2 ft typed
    _wait_edit(page, v)
    assert spec()["y"] == 0.6096
    page_units(page, "imperial")
    page.wait_for_function(f"document.querySelector('{x}').value === '12.5'")


def test_a_refused_trim_keeps_the_typed_boxes(scenario_page, tmp_path):
    """A11: a trim the server refuses (here it can't write the trimmed track) keeps what was typed in the Trim boxes,
    also when Trim is pressed after leaving them (from the keyboard: Tab, then Enter)."""
    track = tmp_path / "fake_track"             # scenario_page puts the scenario in tmp_path
    shutil.copytree(REPO / "tests" / "scenarios" / "fake_track", track)
    page, _ = scenario_page("fake_track.toml")
    page.wait_for_selector("#track-canvas")
    track.chmod(0o555)                           # the trimmed copy can't be written beside it
    page.click("#step-row-0")
    page.wait_for_selector("#trim-start")
    page.fill("#trim-start", "0.05")
    page.fill("#trim-end", "0.08")
    page.evaluate("document.querySelector('#trim-end').blur()")   # left: Trim is pressed with no box focused
    page.wait_for_timeout(50)
    page.evaluate("document.querySelector('#trim-start').dataset.old = '1'")
    page.evaluate("document.querySelector('#trim-go').click()")
    page.wait_for_function("__scrimmage.scenario.lastErrors.length > 0")
    page.wait_for_selector("#trim-start:not([data-old])")   # the refusal drew the step editor again
    try:
        assert page.input_value("#trim-start") == "0.05" and page.input_value("#trim-end") == "0.08"
        assert "can't write the trimmed track" in page.inner_text("#step-editor")
    finally:
        track.chmod(0o755)
