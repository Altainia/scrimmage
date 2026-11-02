"""Browser tests of the Test tab's block editor (`scrimmage edit t.test.toml`), headless Chromium through Playwright.

The fixtures (Chromium lookup, the Server helper) live in tests/test_assembler_browser.py. The robot is the stand-in
box with the stock arm (motor and joint arm.pivot), as in tests/test_test_server.py.
"""
import json
import shutil
import tomllib
from pathlib import Path

import pytest

from scrimmage.assembler.templates import new_config
from test_assembler_browser import CONTRAST_JS, Server, browser  # noqa: F401  (fixtures)

pytestmark = pytest.mark.browser

ARM = """
[[part]]
name = "arm"
stock = "arm"
attach = "drive.top"
ids = { pivot = "PWM:2" }
"""
SPIN = """
[[block]]
do = "spin"
motor = "arm.pivot"
volts = 2.0

[[block]]
do = "wait"
seconds = 0.5
"""
# every kind of block and condition, with the settings that have no box of their own
EVERYTHING = """
[[block]]
do = "move"
joint = "arm.pivot"
to = 30.0
unit = "deg"
timeout = 3.0

[[block]]
do = "drive"
distance = 0.3
volts = 5.0
timeout = 4.0

[[block]]
do = "drive"
seconds = 1.5

[[block]]
do = "stop"
motor = "all"

[[block]]
do = "wait_until"
until = { tilt = { below = 10.0 } }
timeout = 2.0

[[block]]
do = "repeat"
times = 2

  [[block.body]]
  do = "spin"
  motor = "arm.pivot"
  volts = -3.0

  [[block.body]]
  do = "repeat"
  until = { joint = { name = "arm.pivot", above = 40.0, unit = "deg" } }
  max = 5

    [[block.body.body]]
    do = "wait"
    seconds = 0.1

[[block]]
do = "expect"
that = { touch = { robot = "arm.pivot_link", field = "floor" } }
throughout = false

[[block]]
do = "expect"
that = { piece_in = { body = "drive.chassis", piece = "any" } }
throughout = true

[[block]]
do = "wait_until"
until = { hanging_from = "next bar" }

[[block]]
do = "wait_until"
until = { level = "high" }

[[block]]
do = "drive"
distance = 0.5
direction = "left"
volts = 4.0

[[block]]
do = "drive"
seconds = 1.0
direction = "back"

[[block]]
do = "drive"
distance = 0.2
direction = "forward"

[[block]]
do = "turn"
degrees = 90.0
volts = 3.0
timeout = 2.0

[[block]]
do = "turn"
degrees = -45.0

[[block]]
do = "start_feeding"
station = "feeder"

[[block]]
do = "feed_one"
station = "feeder"
timeout = 4.0

[[block]]
do = "stop_feeding"
station = "feeder"

[[block]]
do = "wait_until"
until = { in_hopper = { at_least = 5 } }
timeout = 20.0

[[block]]
do = "expect"
that = { in_hopper = { at_most = 2 } }

[[block]]
do = "wait_until"
until = { station_empty = "feeder" }

[[block]]
do = "expect"
that = { made_it_in = { at_least = 80.0 } }
"""
MISSING = """
[[block]]
do = "wait"
seconds = 0.2

[[block]]
do = "move"
joint = "arm.crank"
to = 40.0
unit = "deg"
"""
READY = ("window.__scrimmageTest && __scrimmageTest.state && __scrimmageTest.workspace"
         " && __scrimmageTest.workspace.getAllBlocks(false).length > 0")


def _write(folder, blocks):
    (folder / "robot.toml").write_text(new_config("standin") + ARM, encoding="utf-8")
    path = folder / "t.test.toml"
    path.write_text(f'[test]\nrobot = "robot.toml"\n{blocks}', encoding="utf-8")
    return path


@pytest.fixture
def test_page(browser, tmp_path, monkeypatch):
    """open(blocks, scheme="light", viewport=(1500, 950), path=None) -> (page, the test file: `path`, else one with
    `blocks` on the stand-in with the arm); everything is torn down afterwards."""
    monkeypatch.setenv("SCRIMMAGE_SKIP_JAVA", "1")
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    servers, contexts, logs = [], [], []

    def open_(blocks=SPIN, scheme="light", viewport=(1500, 950), path=None):
        path = path or _write(tmp_path, blocks)
        srv = Server(path, cwd=tmp_path)
        servers.append(srv)
        ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]}, color_scheme=scheme)
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


def _types(page):
    return page.evaluate("__scrimmageTest.workspace.getTopBlocks(true)[0].getDescendants(true).map(b => b.type)")


def _file_blocks(path):
    return tomllib.loads(path.read_text(encoding="utf-8")).get("block", [])


def test_the_test_tab_opens_on_the_test_and_shows_its_blocks(test_page):
    page, _ = test_page()
    assert page.evaluate("__scrimmage.tab") == "test"
    assert page.locator("#tab-btn-test").is_visible()
    assert _types(page) == ["spin", "wait"]
    text = page.inner_text("#test-blockly")
    assert "spin" in text and "arm.pivot" in text and "wait" in text
    assert page.evaluate("__scrimmageTest.toJSON()") == page.evaluate("__scrimmageTest.state.test")
    assert page.inner_text("#test-saved") == "✓ All changes saved"
    assert page.is_enabled("#test-run")
    assert page.inner_text("#file-path").startswith("t.test.toml")


def test_every_kind_of_block_round_trips_exactly(test_page):
    page, _ = test_page(EVERYTHING)
    st = page.evaluate("__scrimmageTest.state.test")
    assert len(st["blocks"]) == 22
    assert page.evaluate("__scrimmageTest.toJSON()") == st
    # the condition blocks sit in their sockets
    types = page.evaluate("__scrimmageTest.workspace.getAllBlocks(false).map(b => b.type)")
    for kind in ("cond_tilt", "cond_joint", "cond_touch", "cond_piece_in", "cond_hanging_from", "cond_level",
                 "cond_in_hopper", "cond_station_empty", "cond_made_it_in"):
        assert kind in types, kind


def test_an_old_tank_drive_block_gains_no_direction_on_a_round_trip(test_page):
    """A drive block written before directions existed has none; loading it into the editor and reading it back
    must not add a forward."""
    page, _ = test_page("""
[[block]]
do = "drive"
distance = 0.5

[[block]]
do = "drive"
seconds = 1.0
""")
    st = page.evaluate("__scrimmageTest.toJSON()")
    assert [b["do"] for b in st["blocks"]] == ["drive", "drive"]
    assert all("direction" not in b for b in st["blocks"])
    assert st == page.evaluate("__scrimmageTest.state.test")


def _flyout_types(page):
    return page.evaluate("__scrimmageTest.workspace.getFlyout().getWorkspace().getTopBlocks(true).map(b => b.type)")


def test_the_station_blocks_show_on_a_station_field_only(test_page):
    page, path = test_page()
    assert "start_feeding" not in _flyout_types(page)
    robot = path.parent / "robot.toml"
    base = robot.read_text(encoding="utf-8")
    robot.write_text(base + '\n[range]\nfile = "station"\nstart = "at_station"\n', encoding="utf-8")
    page.wait_for_function("__scrimmageTest.state.palette.stations.includes('feeder')")
    types = _flyout_types(page)
    for t in ("start_feeding", "stop_feeding", "feed_one", "cond_station_empty", "turn", "cond_in_hopper",
              "cond_made_it_in"):
        assert t in types, t
    robot.write_text(base + '\n[range]\nfile = "ladder"\nstart = "below"\n', encoding="utf-8")
    page.wait_for_function("__scrimmageTest.state.palette.stations.length === 0")
    types = _flyout_types(page)
    assert "start_feeding" not in types and "cond_station_empty" not in types
    assert "cond_in_hopper" in types and "cond_made_it_in" in types


def _drive_choices(page):
    return page.evaluate("""(() => {
      const fl = __scrimmageTest.workspace.getFlyout().getWorkspace();
      const b = fl.getTopBlocks(true).find(b => b.type === 'drive');
      return b.getField('DIR').getOptions(false).map(o => o[1]);
    })()""")


def test_drive_offers_left_and_right_on_a_swerve_robot_only(test_page):
    page, path = test_page()
    assert page.evaluate("__scrimmageTest.state.palette.drive_kind") == "tank"
    assert _drive_choices(page) == ["forward", "back"]
    (path.parent / "robot.toml").write_text(new_config("swerve"), encoding="utf-8")
    page.wait_for_function("__scrimmageTest.state.palette.drive_kind === 'swerve'")
    assert _drive_choices(page) == ["forward", "back", "left", "right"]


def test_a_wait_dragged_from_the_palette_is_saved(test_page):
    page, path = test_page()
    flyout = page.evaluate("""(() => {
      const fl = __scrimmageTest.workspace.getFlyout().getWorkspace();
      const b = fl.getTopBlocks(true).find(b => b.type === 'wait');
      const r = b.getSvgRoot().getBoundingClientRect();
      return {x: r.left + 8, y: r.top + 8};
    })()""")
    target = page.evaluate("""(() => {
      const ws = __scrimmageTest.workspace;
      const last = ws.getTopBlocks(true)[0].getDescendants(true).at(-1);
      const r = last.getSvgRoot().getBoundingClientRect();
      return {x: r.left + 8, y: r.bottom + 8};
    })()""")
    page.mouse.move(flyout["x"], flyout["y"])
    page.mouse.down()
    for i in range(1, 21):
        page.mouse.move(flyout["x"] + (target["x"] - flyout["x"]) * i / 20,
                        flyout["y"] + (target["y"] - flyout["y"]) * i / 20)
    page.mouse.up()
    page.wait_for_function("__scrimmageTest.workspace.getTopBlocks(true)[0].getDescendants(true).length === 3")
    assert _types(page) == ["spin", "wait", "wait"]
    # type 2 into the new block's seconds box
    box = page.evaluate("""(() => {
      const b = __scrimmageTest.workspace.getTopBlocks(true)[0].getDescendants(true).at(-1);
      const r = b.getField('SECONDS').getSvgRoot().getBoundingClientRect();
      return {x: r.left + r.width / 2, y: r.top + r.height / 2};
    })()""")
    page.mouse.click(box["x"], box["y"])
    page.wait_for_selector(".blocklyHtmlInput")
    page.keyboard.press("Control+A")
    page.keyboard.type("2")
    page.keyboard.press("Enter")
    page.evaluate("__scrimmageTest.saved")
    page.wait_for_function("__scrimmageTest.state.test.blocks.length === 3")
    assert _file_blocks(path)[-1] == {"do": "wait", "seconds": 2.0}
    page.wait_for_function("document.querySelector('#test-saved').textContent === '✓ All changes saved'")


def test_the_motor_picker_lists_the_palettes_motors(test_page):
    page, _ = test_page()
    motors = page.evaluate("__scrimmageTest.state.palette.motors.map(m => m.name)")
    assert "arm.pivot" in motors
    box = page.evaluate("""(() => {
      const b = __scrimmageTest.workspace.getTopBlocks(true)[0];
      const r = b.getField('MOTOR').getSvgRoot().getBoundingClientRect();
      return {x: r.left + r.width / 2, y: r.top + r.height / 2};
    })()""")
    page.mouse.click(box["x"], box["y"])
    page.wait_for_selector(".blocklyDropDownDiv .blocklyMenuItem")
    items = page.locator(".blocklyDropDownDiv .blocklyMenuItem")
    assert [items.nth(i).inner_text().strip() for i in range(items.count())] == motors
    page.keyboard.press("Escape")


def test_a_missing_joint_warns_on_its_block_and_run_is_off(test_page):
    page, _ = test_page(MISSING)
    page.wait_for_function("__scrimmageTest.state.problems.length > 0")
    msg = page.evaluate("__scrimmageTest.state.problems[0]")
    assert msg["place"] == [1] and "arm.crank" in msg["message"]
    warning = page.evaluate("""(() => {
      const b = __scrimmageTest.workspace.getTopBlocks(true)[0].getDescendants(true)[1];
      const icon = b.getIcon(Blockly.icons.IconType.WARNING);
      return icon ? icon.getText() : null;
    })()""")
    assert warning and "arm.crank" in warning
    assert page.evaluate("__scrimmageTest.workspace.getTopBlocks(true)[0].getIcon(Blockly.icons.IconType.WARNING)") is None
    assert "arm.crank" in page.inner_text("#test-problems")
    assert page.is_disabled("#test-run")
    # the name the robot doesn't have is kept, not swapped for one it has
    assert page.evaluate("__scrimmageTest.toJSON().blocks[1].joint") == "arm.crank"


def test_an_empty_condition_socket_is_not_saved_and_blocks_run(test_page):
    page, path = test_page("""
[[block]]
do = "wait_until"
until = { tilt = { below = 10.0 } }
""")
    before = path.read_text(encoding="utf-8")
    page.evaluate("__scrimmageTest.workspace.getTopBlocks(true)[0].getInputTargetBlock('COND').dispose()")
    page.wait_for_function("document.querySelector('#test-saved').textContent.startsWith('Not saved yet')")
    page.evaluate("__scrimmageTest.saved")
    assert path.read_text(encoding="utf-8") == before
    assert page.is_disabled("#test-run")
    assert "Plug a check" in page.inner_text("#test-problems")


def test_a_save_after_an_outside_edit_shows_the_saved_version(test_page):
    page, path = test_page()
    outside = path.read_text(encoding="utf-8").replace("volts = 2.0", "volts = 5.0")
    # the file changes outside, then the page changes a block before it has heard of that
    page.evaluate("""(() => {
      __scrimmageTest.workspace.getTopBlocks(true)[0].getNextBlock().setFieldValue(3, 'SECONDS');
    })()""")
    path.write_text(outside, encoding="utf-8")
    page.wait_for_selector("#notices .notice:has-text('The test changed on disk; showing the saved version.')")
    page.wait_for_function("__scrimmageTest.toJSON().blocks[0].volts === 5")
    assert page.evaluate("__scrimmageTest.toJSON().blocks[1].seconds") == 0.5
    assert _file_blocks(path)[0]["volts"] == 5.0


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_test_tab_meets_wcag_aa(test_page, scheme):
    page, _ = test_page(MISSING + SPIN, scheme)
    page.wait_for_function("__scrimmageTest.state.problems.length > 0")
    page.wait_for_timeout(300)
    dark = page.evaluate(f"({CONTRAST_JS}).lum(getComputedStyle(document.body).backgroundColor) < 0.2")
    assert dark == (scheme == "dark")
    assert page.evaluate(f"({CONTRAST_JS}).blocks(document.body)") > 10   # the block text was checked
    assert page.evaluate(f"({CONTRAST_JS}).check(document.body)") == []
    if scheme == "light":   # the blocks follow the page when the computer switches to dark
        page.emulate_media(color_scheme="dark")
        page.wait_for_function("__scrimmageTest.workspace.getTheme().name === 'scrimmage-dark'")
        assert page.evaluate(f"({CONTRAST_JS}).check(document.body)") == []


def _click_field(page, block_js, field):
    box = page.evaluate(f"""(() => {{
      const r = ({block_js}).getField('{field}').getSvgRoot().getBoundingClientRect();
      return {{x: r.left + r.width / 2, y: r.top + r.height / 2}};
    }})()""")
    page.mouse.click(box["x"], box["y"])


def test_a_save_the_server_refuses_shows_on_its_block_and_nothing_runs(test_page):
    """-0.5 m is fine, -0.5 s isn't: the server refuses the save, the drive block says why, and Run stays off (the
    file still holds the last good test, which Run must not run)."""
    page, path = test_page("""
[[block]]
do = "wait"
seconds = 0.2

[[block]]
do = "drive"
distance = 0.3
""")
    drive = "__scrimmageTest.workspace.getTopBlocks(true)[0].getNextBlock()"
    _click_field(page, drive, "AMOUNT")
    page.wait_for_selector(".blocklyHtmlInput")
    page.keyboard.press("Control+A")
    page.keyboard.type("-0.5")
    page.keyboard.press("Enter")
    page.evaluate("__scrimmageTest.saved")
    page.wait_for_function("__scrimmageTest.state.test.blocks[1].distance === -0.5")
    assert page.is_enabled("#test-run")
    _click_field(page, drive, "WHAT")
    page.click(".blocklyDropDownDiv .blocklyMenuItem:has-text('s')")
    page.wait_for_function("document.querySelector('#test-saved').textContent.startsWith('Not saved')")
    warning = page.evaluate(f"(() => {{ const i = ({drive}).getIcon(Blockly.icons.IconType.WARNING); return i ? i.getText() : null; }})()")
    assert warning and "negative" in warning
    assert "negative" in page.inner_text("#test-problems")
    assert page.is_disabled("#test-run")
    page.evaluate("document.querySelector('#test-run').disabled = false; document.querySelector('#test-run').click()")
    page.wait_for_timeout(1500)
    assert page.evaluate("__scrimmageTest.state.runs") == [] and page.evaluate("__scrimmageTest.state.running") is None
    assert _file_blocks(path)[1] == {"do": "drive", "distance": -0.5}


def test_the_touch_picker_offers_the_fields_surfaces_and_marks_missing_names_by_side(test_page):
    page, _ = test_page("""
[[block]]
do = "wait_until"
until = { touch = { robot = "drive.chassis", field = "carpet" } }

[[block]]
do = "wait_until"
until = { touch = { robot = "arm.gone", field = "nowhere" } }
""")
    targets = page.evaluate("__scrimmageTest.state.palette.targets")
    assert "carpet" in targets and "wall_left" in targets
    opts = page.evaluate("""(() => {
      const [a, b] = __scrimmageTest.workspace.getTopBlocks(true)[0].getDescendants(true).filter(x => x.type === 'cond_touch');
      const labels = (blk, f) => blk.getField(f).getOptions(false).map(o => o[0]);
      return {field: labels(a, 'FIELD'), robot: labels(b, 'ROBOT'), missingField: labels(b, 'FIELD')};
    })()""")
    assert "carpet" in opts["field"] and "wall_left" in opts["field"]
    assert not [o for o in opts["field"] if "not on" in o and "nowhere" not in o]
    assert "arm.gone (not on the robot)" in opts["robot"]
    assert "nowhere (not on the field)" in opts["missingField"]
    # nothing is wrong with touching the carpet: only the second block has problems
    page.wait_for_function("__scrimmageTest.state.problems.length > 0")
    assert {tuple(p["place"]) for p in page.evaluate("__scrimmageTest.state.problems")} == {(1,)}


def test_the_status_bar_counts_every_block_of_the_test(test_page):
    page, _ = test_page(EVERYTHING)
    page.wait_for_function("document.querySelector('#test-count').textContent === '25 blocks in the test'")


def test_dragging_a_test_apart_keeps_the_first_block_and_a_reload_says_what_it_left_out(test_page):
    page, path = test_page(SPIN + """
[[block]]
do = "wait"
seconds = 0.7
""")
    # the two blocks under the first, pulled off together: the bigger stack, but not the test
    page.evaluate("""(() => {
      const second = __scrimmageTest.workspace.getTopBlocks(true)[0].getNextBlock();
      second.unplug(false);
      second.moveBy(0, 200);
    })()""")
    page.wait_for_function("__scrimmageTest.state.test.blocks.length === 1")
    assert page.evaluate("__scrimmageTest.toJSON().blocks") == [{"do": "spin", "motor": "arm.pivot", "volts": 2}]
    assert _file_blocks(path) == [{"do": "spin", "motor": "arm.pivot", "volts": 2.0}]
    assert page.is_disabled("#test-run")
    assert "joined to the test" in page.inner_text("#test-problems")
    # an outside edit reloads the test: the loose blocks go, and the page says so
    path.write_text(path.read_text(encoding="utf-8").replace("volts = 2.0", "volts = 4.0"), encoding="utf-8")
    page.wait_for_selector("#notices .notice:has-text('Blocks not joined to your test were left out: wait 0.5 s')")
    page.wait_for_function("__scrimmageTest.workspace.getTopBlocks(false).length === 1")
    assert page.is_enabled("#test-run")


# -- the run panel: replay, chart and compare ------------------------------------------------------------------------

POSES = "__scrimmageTest.replay.view.groups.map(g => [...g.position.toArray(), ...g.quaternion.toArray()])"


def _sentence(s):
    return s[0].upper() + s[1:] + ("" if s[-1] in ".!?" else ".")


def _run(page, run_id):
    page.wait_for_function("!document.querySelector('#test-run').disabled")
    page.click("#test-run")
    page.wait_for_function(f"__scrimmageTest.replay.runId === {run_id!r} && __scrimmageTest.state.running === null")
    return page.evaluate("__scrimmageTest.state.runs[0]")


def _moved(a, b):
    return max(abs(x - y) for ga, gb in zip(a, b) for x, y in zip(ga, gb))


def test_a_run_shows_its_outcome_and_a_replay_that_plays_and_scrubs(test_page):
    page, _ = test_page()
    # before the first run, one line says what to do
    page.wait_for_selector("#test-run-empty", state="visible")
    assert page.inner_text("#test-run-empty") == "Press Run to try your test. The replay and chart show up here."
    run = _run(page, "001")
    assert page.is_hidden("#test-run-empty")
    assert run["status"] == "finished"
    assert page.inner_text("#test-result-title") == "Run 1 · finished"
    assert run["outcome"] == "finished" and page.inner_text("#test-outcome") == "Every block played."
    assert page.evaluate("__scrimmageTest.replay.playing") is False
    start = page.evaluate(POSES)
    t0 = page.evaluate("__scrimmageTest.replay.time")
    assert float(page.input_value("#test-scrub")) == pytest.approx(t0)
    page.select_option("#test-speed", "0.25")
    page.click("#test-play")
    page.wait_for_function("__scrimmageTest.replay.playing"
                           f" && Number(document.querySelector('#test-scrub').value) > {t0} + 0.02")
    page.click("#test-play")
    assert page.evaluate("__scrimmageTest.replay.playing") is False
    # drag the scrubber to its end: the arm has swung, so a mover is drawn somewhere else
    box = page.locator("#test-scrub").bounding_box()
    y = box["y"] + box["height"] / 2
    page.mouse.move(box["x"] + 4, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2, y, steps=5)
    page.mouse.move(box["x"] + box["width"] + 20, y, steps=5)
    page.mouse.up()
    t_end = page.evaluate("Number(document.querySelector('#test-scrub').max)")
    assert t_end > t0 + 0.4
    assert page.evaluate("__scrimmageTest.replay.time") == pytest.approx(t_end)
    assert page.inner_text("#test-time") == f"{t_end:.1f} s of {t_end:.1f} s"
    assert _moved(start, page.evaluate(POSES)) > 1e-3
    # the runs list has it, with its outcome
    assert page.locator("#test-runs .test-run-item").count() == 1
    assert "Run 1" in page.inner_text("#test-runs") and "Every block played." in page.inner_text("#test-runs")


def test_a_second_run_is_charted_against_the_first(test_page):
    page, _ = test_page()
    _run(page, "001")
    chart = page.evaluate("__scrimmageTest.replay.chart")
    assert (chart["signals"], chart["runs"]) == (["tilt", "arm.pivot"], ["001", None])
    _run(page, "002")
    page.wait_for_function("__scrimmageTest.replay.chart.runs[1] === '001'")
    chart = page.evaluate("__scrimmageTest.replay.chart")
    assert (chart["signals"], chart["runs"]) == (["tilt", "arm.pivot"], ["002", "001"])
    plots = "[...document.querySelectorAll('#test-chart canvas')].map(c => [c.dataset.signal, c.dataset.series])"
    assert page.evaluate(plots) == [["tilt", "2"], ["arm.pivot", "2"]]
    assert page.inner_text("#test-chart-key").replace("\n", " ").split() == ["Run", "2", "Run", "1", "(faded)"]
    assert page.input_value("#test-compare") == "001"
    # the two runs' outcomes, next to the chart
    assert page.inner_text("#test-chart-outcomes").splitlines() == ["Run 2 · finished: Every block played.",
                                                                    "Run 1 · finished: Every block played."]
    # the signal picker adds the arm's current
    page.click("#test-signals [data-signal='arm.pivot.current']")
    assert page.evaluate("__scrimmageTest.replay.chart.signals") == ["tilt", "arm.pivot", "arm.pivot.current"]
    assert page.locator("#test-chart canvas").count() == 3
    # compare with nothing: one line each
    page.select_option("#test-compare", "")
    page.wait_for_function("__scrimmageTest.replay.chart.runs[1] === null")
    assert page.evaluate("[...document.querySelectorAll('#test-chart canvas')].every(c => c.dataset.series === '1')")
    assert page.is_hidden("#test-chart-outcomes")
    # the newest first; clicking the older run shows it, compared with nothing older
    items = page.locator("#test-runs .test-run-item")
    assert [items.nth(i).get_attribute("data-run") for i in range(items.count())] == ["002", "001"]
    items.nth(1).click()
    page.wait_for_function("__scrimmageTest.replay.runId === '001'")
    assert page.inner_text("#test-result-title") == "Run 1 · finished"
    assert items.nth(1).get_attribute("aria-current") == "true"
    # a click on a plot moves the replay there
    box = page.locator("#test-chart canvas").first.bounding_box()
    page.mouse.click(box["x"] + box["width"] - 9, box["y"] + 20)
    assert page.evaluate("__scrimmageTest.replay.time") == pytest.approx(
        page.evaluate("Number(document.querySelector('#test-scrub').max)"), abs=0.05)


def test_a_timeout_the_tab_cant_edit_is_named_in_full(test_page):
    """A wait's timeout has no box on the block: the outcome sentence names the block in full, and a note says where
    the timeout is set."""
    page, _ = test_page("""
[[block]]
do = "wait_until"
until = { tilt = { above = 45.0 } }
timeout = 0.3
""")
    run = _run(page, "001")
    assert run["status"] == "timed out" and "(block 1)" in run["outcome"], run
    assert page.inner_text("#test-result-title") == "Run 1 · timed out"
    assert page.inner_text("#test-outcome") == _sentence(run["outcome"])
    assert page.is_visible("#test-outcome-note") and "timeout" in page.inner_text("#test-outcome-note")
    assert _sentence(run["outcome"]) in page.inner_text("#test-runs")


def test_stop_ends_the_run_and_the_list_says_it_was_stopped(test_page):
    page, _ = test_page("""
[[block]]
do = "wait"
seconds = 30.0
""")
    page.click("#test-run")
    page.wait_for_selector("#test-stop:not([hidden])")
    page.wait_for_function("(__scrimmageTest.state.running || {}).progress > 0")
    page.click("#test-stop")
    page.wait_for_function("__scrimmageTest.replay.runId === '001'")
    assert page.is_hidden("#test-stop")
    assert page.inner_text("#test-result-title") == "Run 1 · stopped"
    assert page.inner_text("#test-outcome").startswith("You stopped the run at")
    assert "stopped" in page.inner_text("#test-runs .test-run-item")


WEDGE_EXTENTS = """(() => {
  const out = [];
  for (const g of __scrimmageTest.replay.view.groups) g.traverse((o) => {
    if (!o.isMesh || o.geometry.type !== 'BufferGeometry') return;
    o.geometry.computeBoundingBox();
    const b = o.geometry.boundingBox;
    out.push(Math.max(b.max.x - b.min.x, b.max.y - b.min.y, b.max.z - b.min.z));
  });
  return out;
})()"""


def test_an_old_run_replays_its_own_geometry_after_the_part_changes(test_page, tmp_path):
    """Review Focus 5: the part's mesh and its config replaced (Import again) between two runs; the first run still
    draws its own robot, and the second draws the new one."""
    import os

    from test_test_server import write_wedge
    page, _ = test_page()
    robot = tmp_path / "robot.toml"
    write_wedge(tmp_path / "wedge", 0.1)
    robot.write_text(robot.read_text(encoding="utf-8")
                     + '\n[[part]]\nname = "w"\npath = "wedge"\nattach = "drive.top"\n', encoding="utf-8")
    page.wait_for_function("__scrimmageTest.state.palette.parts.includes('w')")
    _run(page, "001")
    old = min(page.evaluate(WEDGE_EXTENTS))
    write_wedge(tmp_path / "wedge", 0.2, mass=2.0)
    stl = tmp_path / "wedge" / "wedge.stl"
    st = stl.stat()                  # Import again comes seconds later, not within MuJoCo's one-second mesh cache
    os.utime(stl, (st.st_atime + 5, st.st_mtime + 5))
    _run(page, "002")
    new = min(page.evaluate(WEDGE_EXTENTS))
    assert new == pytest.approx(2 * old, rel=1e-3)
    page.click("#test-runs [data-run='001']")
    page.wait_for_function("__scrimmageTest.replay.runId === '001'")
    assert min(page.evaluate(WEDGE_EXTENTS)) == pytest.approx(old, rel=1e-3)
    assert page.inner_text("#test-result-title") == "Run 1 · finished"


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_run_panel_meets_wcag_aa(test_page, scheme):
    page, _ = test_page(SPIN + """
[[block]]
do = "expect"
that = { tilt = { below = 20.0 } }
""", scheme)
    _run(page, "001")
    _run(page, "002")
    page.wait_for_function("__scrimmageTest.replay.chart.runs[1] === '001'")
    page.click("#test-signals [data-signal='arm.pivot.current']")
    assert page.is_visible("#test-checks li")
    assert page.evaluate(f"({CONTRAST_JS}).check(document.querySelector('#test-run-col'))") == []


def _hex_ratio(a, b):
    def lum(h):
        h = h.strip().lstrip("#")
        c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    x, y = lum(a), lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_charts_lines_are_3_to_1_on_its_background(test_page, scheme):
    """WCAG 1.4.11 for the plotted lines (canvas pixels the DOM check can't see): the colors the chart draws with,
    the compared run's faded line included, against the plot's background."""
    page, _ = test_page(SPIN, scheme)
    _run(page, "001")
    colors = page.evaluate("__scrimmageTest.replay.chart.colors")
    assert _hex_ratio(colors["compare"], colors["bg"]) >= 3, colors
    assert _hex_ratio(colors["run"], colors["bg"]) >= 3, colors


def test_a_run_that_cant_load_shows_the_error_in_its_place(test_page, tmp_path):
    page, _ = test_page()
    _run(page, "001")
    _run(page, "002")
    motion = tmp_path / "t-runs" / "001" / "motion.bin"
    motion.write_bytes(motion.read_bytes()[:-10])
    page.click("#test-runs [data-run='001']")
    page.wait_for_selector("#test-load-error:not([hidden])")
    assert page.inner_text("#test-load-error") == "Couldn't load Run 1: this run's replay file is damaged; run the test again"
    assert page.is_hidden("#test-results") and page.evaluate("__scrimmageTest.replay.runId") is None
    assert page.get_attribute("#test-runs button[data-run='001']", "aria-current") == "true"
    page.click("#test-runs [data-run='002']")
    page.wait_for_function("__scrimmageTest.replay.runId === '002'")
    assert page.is_hidden("#test-load-error") and page.is_visible("#test-results")
    # the first run the page loads can't load either: the error still shows
    motion = tmp_path / "t-runs" / "002" / "motion.bin"
    motion.write_bytes(b"nope")
    page.reload()
    page.wait_for_selector("#test-load-error:not([hidden])")
    assert "Run 2" in page.inner_text("#test-load-error") and page.is_hidden("#test-results")


def test_a_run_number_used_again_shows_the_new_run(test_page, tmp_path):
    """The runs folder deleted: numbering starts again at 001, and the page shows the new run 001, not the one it
    loaded before under that number."""
    import shutil
    page, path = test_page()
    _run(page, "001")
    assert page.inner_text("#test-result-title") == "Run 1 · finished"
    shutil.rmtree(tmp_path / "t-runs")
    page.wait_for_function("__scrimmageTest.state.runs.length === 0")
    path.write_text('[test]\nrobot = "robot.toml"\n[[block]]\ndo = "wait_until"\n'
                    'until = { tilt = { above = 45.0 } }\ntimeout = 0.3\n', encoding="utf-8")
    page.wait_for_function("__scrimmageTest.state.test.blocks[0].do === 'wait_until'")
    _run(page, "001")
    page.wait_for_function("document.querySelector('#test-result-title').textContent === 'Run 1 · timed out'")
    assert "timed out" in page.inner_text("#test-outcome")


def test_a_run_button_keeps_the_keyboard_focus_while_a_run_goes(test_page):
    page, _ = test_page("""time_limit = 600.0

[[block]]
do = "wait"
seconds = 500.0
""")

    def start():
        page.click("#test-run")
        page.wait_for_selector("#test-stop:not([hidden])")
        page.wait_for_function("(__scrimmageTest.state.running || {}).progress > 0")
    start()
    page.click("#test-stop")
    page.wait_for_function("__scrimmageTest.replay.runId === '001'")
    start()
    page.focus("#test-runs button[data-run='001']")
    v = page.evaluate("__scrimmageTest.state.version")
    page.wait_for_function(f"__scrimmageTest.state.version >= {v} + 3")     # a few progress states
    assert page.evaluate("document.activeElement.dataset.run") == "001"
    page.evaluate("document.querySelector('#test-stop').click()")      # (a click would take the focus)
    page.wait_for_function("__scrimmageTest.replay.runId === '002'")
    assert page.evaluate("document.activeElement.dataset.run") == "001"


CHECKED = SPIN + """
[[block]]
do = "expect"
that = { tilt = { below = 20.0 } }
"""


def _sweep_block(page, values=("1", "3", "3")):
    """Right-click the test's first block (spin), pick "Try values…", and try from/to/steps."""
    box = page.evaluate("""(() => {
      const r = __scrimmageTest.workspace.getTopBlocks(true)[0].getSvgRoot().getBoundingClientRect();
      return {x: r.left + 14, y: r.top + 12};
    })()""")
    page.mouse.click(box["x"], box["y"], button="right")
    page.locator(".blocklyMenuItem", has_text="Try values…").click()
    page.wait_for_selector("#test-sweep-dialog[open]")
    assert "spin arm.pivot" in page.inner_text("#test-sweep-for")
    for sel, v in zip(("#test-sweep-from", "#test-sweep-to", "#test-sweep-steps"), values):
        page.fill(sel, v)
    page.click("#test-sweep-go")
    page.wait_for_function("!document.querySelector('#test-sweep-dialog').open")
    page.wait_for_function("__scrimmageTest.sweep && __scrimmageTest.state.running === null"
                           " && __scrimmageTest.sweep.rows === 3")


def test_a_sweep_of_a_blocks_volts_shows_a_row_per_value_and_a_row_replays_its_run(test_page):
    page, path = test_page(CHECKED)
    _sweep_block(page)
    rows = page.locator("#test-sweep-table tbody tr")
    assert rows.count() == 3
    assert [rows.nth(k).locator("td").first.inner_text() for k in range(3)] == ["1", "2", "3"]
    assert "spin arm.pivot (block 1): volts (V)" in page.inner_text("#test-sweep-title")
    assert rows.nth(0).locator("td.test-check-ok").count() == 1           # the expect, passed
    # the chart: a point per value against when its run ended
    sweep = page.evaluate("__scrimmageTest.sweep")
    assert sweep["y"] == "t_end" and [p[0] for p in sweep["points"]] == [1, 2, 3]
    # the newest run is the sweep's last; a click on the second row shows its run
    page.wait_for_function("__scrimmageTest.replay.runId === 'sweep-001/003'")
    page.click("#test-sweep-table [data-run='sweep-001/002']")
    page.wait_for_function("__scrimmageTest.replay.runId === 'sweep-001/002'")
    assert page.inner_text("#test-result-title") == "Sweep 1, run 2 · finished"
    assert rows.nth(1).get_attribute("aria-current") == "true"
    # a signal's value at the end: the arm turned further at more volts
    page.select_option("#test-sweep-y", "arm.pivot")
    page.wait_for_function("__scrimmageTest.sweep.points.length === 3")
    ys = [p[1] for p in page.evaluate("__scrimmageTest.sweep.points")]
    assert ys[0] < ys[1] < ys[2], ys
    # the file never changed, and a plain run hides the sweep again
    assert _file_blocks(path)[0]["volts"] == 2.0
    _run(page, "001")
    assert page.is_hidden("#test-sweep-wrap")


def test_the_sweep_form_refuses_more_than_twelve_values(test_page):
    page, _ = test_page()
    page.evaluate("__scrimmageTest.trySweep(__scrimmageTest.workspace.getTopBlocks(true)[0].id)")
    page.wait_for_selector("#test-sweep-dialog[open]")
    page.fill("#test-sweep-list", "1 2 3 4 5 6 7 8 9 10 11 12 13")
    page.click("#test-sweep-go")
    assert "at most 12 values" in page.inner_text("#test-sweep-errors")
    page.fill("#test-sweep-list", "1, two")
    page.click("#test-sweep-go")
    assert "“two” isn't a number" in page.inner_text("#test-sweep-errors")
    assert page.evaluate("__scrimmageTest.state.sweeps.length") == 0
    page.click("#test-sweep-dialog [data-close]")


def test_a_sweep_is_refused_while_blocks_lie_loose_as_run_is(test_page):
    page, _ = test_page()
    page.evaluate("""(() => {
      const b = __scrimmageTest.workspace.newBlock('wait');
      b.initSvg(); b.render(); b.moveBy(400, 300);
    })()""")
    page.wait_for_function("document.querySelector('#test-run').disabled")
    page.evaluate("__scrimmageTest.trySweep(__scrimmageTest.workspace.getTopBlocks(true)[0].id)")
    page.wait_for_selector("#test-sweep-dialog[open]")
    page.fill("#test-sweep-list", "1, 2")
    page.click("#test-sweep-go")
    page.wait_for_selector("#test-sweep-errors:not([hidden])")
    assert "Fix the problems first" in page.inner_text("#test-sweep-errors")
    assert page.evaluate("__scrimmageTest.state.running") is None
    assert page.evaluate("__scrimmageTest.state.sweeps.length") == 0
    page.click("#test-sweep-dialog [data-close]")


def _robot_part(path, name):
    return next(p for p in tomllib.loads((path.parent / "robot.toml").read_text(encoding="utf-8"))["part"]
                if p["name"] == name)


def _wait_file(pred, path, timeout=15.0):
    import time
    end = time.monotonic() + timeout
    while not pred():
        assert time.monotonic() < end, path.read_text(encoding="utf-8")
        time.sleep(0.05)


def test_setup_changes_the_stand_ins_weight_and_mounts_in_the_robot_file_and_the_hooks_in_the_test(test_page):
    page, path = test_page()
    assert page.evaluate("document.querySelector('#test-setup').open") is False      # folded away at first
    page.wait_for_function("document.querySelector('#test-setup summary').textContent.includes('robot 45 kg')")
    page.click("#test-setup summary")
    page.wait_for_selector("#test-mass")
    assert page.input_value("#test-mass") == "45"
    page.fill("#test-mass", "30")
    page.press("#test-mass", "Enter")
    _wait_file(lambda: _robot_part(path, "drive").get("mass") == 30.0, path)
    page.wait_for_function("__scrimmageTest.robot.can_undo")          # the Robot tab's Undo takes it back
    page.fill("#test-com-up", "0.1")
    page.press("#test-com-up", "Enter")
    _wait_file(lambda: _robot_part(path, "drive").get("com_offset") == [0.0, 0.0, 0.1], path)
    # the arm's mount, but not the drivetrain's (the Robot tab moves that)
    assert page.locator("#test-setup tr[data-part='arm']").count() == 1
    assert page.locator("#test-setup tr[data-part='dt']").count() == 0
    page.fill("#test-mount-arm-x", "0.05")
    page.press("#test-mount-arm-x", "Enter")
    _wait_file(lambda: _robot_part(path, "arm").get("pos") == [0.05, 0.0, 0.0], path)
    assert _robot_part(path, "arm")["attach"] == "drive.top"
    # a hook: saved in the test
    page.check("#test-setup .test-setup-hooks input[data-body='arm.arm']")
    page.evaluate("__scrimmageTest.saved")
    _wait_file(lambda: tomllib.loads(path.read_text(encoding="utf-8"))["test"].get("hooks") == ["arm.arm"], path)
    page.wait_for_function("__scrimmageTest.state.palette.hooks.includes('arm.arm')")
    assert "hooks: arm.arm" in page.inner_text("#test-setup summary")
    # the hooks offered are the mechanism's: not the drivetrain's or the chassis's
    assert page.locator("#test-setup .test-setup-hooks input[data-body='dt.left_1']").count() == 0
    assert page.locator("#test-setup .test-setup-hooks input[data-body='drive.chassis']").count() == 0
    assert page.locator("#test-setup .test-setup-hooks input[data-body='arm.base']").count() == 1
    # the Robot tab's Undo takes the last Setup edit (the arm's mount) back out of the file
    page.click("#tab-btn-robot")
    page.click("#undo")
    _wait_file(lambda: "pos" not in _robot_part(path, "arm"), path)
    assert _robot_part(path, "drive")["mass"] == 30.0


def test_setup_field_box_says_what_it_changes_and_how_to_undo_it(test_page):
    page, _ = test_page()
    page.click("#test-setup summary")
    hint = page.inner_text("#test-setup .test-setup-field .hint")
    assert "Picking a field moves the robot to that field's start (this changes robot.toml)." in hint
    assert "The Robot tab's Undo puts the old field and start back." in hint


# 900 x 700: a small laptop's window. Wider, the mounts used to share a row with the hooks and were cut off there:
# at 1200 px and at the default 1500 px "Try values…" was 116 px and 26 px past the edge.
@pytest.mark.parametrize("viewport", [(900, 700), (1200, 700), (1500, 950)])
def test_setup_mounts_try_values_is_whole_inside_setup(test_page, viewport):
    """The mounts table (a part name, four boxes and a button) is whole inside Setup: no sideways scrolling to
    reach "Try values…"."""
    page, _ = test_page(viewport=viewport)
    page.click("#test-setup summary")
    page.wait_for_selector("#test-setup [data-try='mount-arm']")
    rects = page.evaluate("""() => {
      const r = (el) => { const b = el.getBoundingClientRect(); return [b.left, b.right]; };
      const body = document.querySelector('#test-setup-body');
      const table = document.querySelector('#test-setup .test-mounts');
      return { button: r(document.querySelector("#test-setup [data-try='mount-arm']")),
               fieldset: r(document.querySelector('#test-setup .test-setup-mounts')), body: r(body),
               table_overflow: table.scrollWidth - table.clientWidth,
               body_overflow: body.scrollWidth - body.clientWidth };
    }""")
    (bl, br), (fl, fr), (pl, pr) = rects["button"], rects["fieldset"], rects["body"]
    assert fl - 0.5 <= bl and br <= fr + 0.5, rects
    assert pl - 0.5 <= bl and br <= pr + 0.5, rects
    assert rects["table_overflow"] <= 0 and rects["body_overflow"] <= 0, rects


def test_setup_try_values_sweeps_the_robots_weight(test_page):
    page, path = test_page()
    page.click("#test-setup summary")
    page.click("#test-setup [data-try='mass']")
    page.wait_for_selector("#test-sweep-dialog[open]")
    assert page.is_hidden("#test-sweep-which-field")
    page.fill("#test-sweep-list", "40, 50")
    page.click("#test-sweep-go")
    page.wait_for_function("__scrimmageTest.sweep && __scrimmageTest.sweep.rows === 2"
                           " && __scrimmageTest.state.running === null")
    assert "the robot's weight (kg)" in page.inner_text("#test-sweep-title")
    assert _robot_part(path, "drive")["mass"] == 45.0       # a sweep changes a copy, never the file


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_setup_and_a_sweep_meet_wcag_aa(test_page, scheme):
    page, _ = test_page(CHECKED, scheme)
    page.click("#test-setup summary")
    page.wait_for_selector("#test-mass")
    _sweep_block(page)
    page.wait_for_timeout(200)
    assert page.evaluate(f"({CONTRAST_JS}).check(document.querySelector('#tab-test'))") == []


EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "blocks"


def _example(tmp_path, name, test, edit=None):
    """A copy of examples/blocks/<name> in tmp_path, and its test file (`edit(text)` -> the text to write instead)."""
    folder = tmp_path / name
    shutil.copytree(EXAMPLES / name, folder)
    path = folder / test
    if edit:
        path.write_text(edit(path.read_text(encoding="utf-8")), encoding="utf-8")
    return path


def _toml(path):
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _open_setup(page):
    page.click("#test-setup summary")
    page.wait_for_selector("#test-field")


def test_setup_lists_every_field_and_a_start_picker_for_fields_with_more_than_one(test_page, tmp_path):
    page, path = test_page(path=_example(tmp_path, "station", "feed.test.toml"))
    _open_setup(page)
    names = page.eval_on_selector_all("#test-field option", "os => os.map(o => o.textContent)")
    assert len(names) == page.evaluate("__scrimmageTest.state.fields.length") and names[0] == "The plain field"
    assert "A human player station" in names
    assert page.input_value("#test-field") == "station"
    assert page.input_value("#test-start") == "at_station"       # the station field has two starts, in words
    assert page.eval_on_selector_all("#test-start option", "os => os.map(o => o.textContent)") == [
        "At station", "Facing ball"]
    robot = path.parent / "hopper-standin.toml"
    page.select_option("#test-field", "2026")
    _wait_file(lambda: _toml(robot)["range"]["file"] == "2026", robot)
    first = page.evaluate("__scrimmageTest.state.fields.find(f => f.file === '2026').starts")
    assert len(first) > 1 and _toml(robot)["range"]["start"] == first[0]        # put at the field's first start
    page.wait_for_function(f"document.querySelector('#test-start') && document.querySelector('#test-start').value"
                           f" === {first[0]!r}")
    page.select_option("#test-start", first[1])
    _wait_file(lambda: _toml(robot)["range"].get("start") == first[1], robot)
    page.select_option("#test-field", "")
    _wait_file(lambda: "range" not in _toml(robot), robot)
    page.wait_for_function("!document.querySelector('#test-start')")             # the plain field has none


def test_setup_hopper_ball_and_station_settings_are_saved_in_the_test(test_page, tmp_path):
    path = _example(tmp_path, "station", "feed.test.toml", lambda s: s.replace('hopper = ["hopper.bin"]\n', ""))
    page, _ = test_page(path=path)
    _open_setup(page)
    hint = page.inner_text("#test-setup .test-setup-hopper .hint")
    assert hint == "Tick the bodies a piece lands in. A piece whose center is inside one made it in."
    box = "#test-setup .test-setup-hopper input[data-body='hopper.bin']"
    assert page.is_checked(box)                       # preselected (starter_hopper) ...
    assert "hopper" not in _toml(path)["test"]        # ... and not saved until Setup changes
    page.uncheck(box)
    page.check(box)
    _wait_file(lambda: _toml(path)["test"].get("hopper") == ["hopper.bin"], path)
    balls = page.eval_on_selector_all("#test-ball option", "os => os.map(o => [o.value, o.textContent])")
    field_ball = page.evaluate("__scrimmageTest.state.palette.ball")
    assert balls[0] == [field_ball, f"{field_ball} (the field's)"] and len(balls) > 2
    page.select_option("#test-ball", "2017-fuel")
    _wait_file(lambda: _toml(path).get("setup", {}).get("ball") == "2017-fuel", path)
    # the field's interval until changed; a change goes in [setup] beside the example's spreads
    shown = page.input_value("#test-station-feeder-interval")
    assert float(shown) == page.evaluate("__scrimmageTest.state.palette.station_settings.feeder.interval")
    assert page.input_value("#test-station-feeder-spread_yaw_deg") == "5"          # the test's own value
    page.fill("#test-station-feeder-interval", "0.5")
    page.press("#test-station-feeder-interval", "Enter")
    _wait_file(lambda: _toml(path)["setup"]["station"]["feeder"].get("interval") == 0.5, path)
    setup = _toml(path)["setup"]
    assert setup["station"]["feeder"] == {"spread_yaw_deg": 5.0, "spread_pitch_deg": 5.0, "spread_speed": 0.2,
                                          "interval": 0.5}
    assert setup["seed"] == 1 and setup["ball"] == "2017-fuel" and _toml(path)["test"]["hopper"] == ["hopper.bin"]
    # the release box is in inches on screen, meters in the file
    page.fill("#test-station-feeder-spread_position-1", "2")
    page.press("#test-station-feeder-spread_position-1", "Enter")
    _wait_file(lambda: "spread_position" in _toml(path)["setup"]["station"]["feeder"], path)
    assert _toml(path)["setup"]["station"]["feeder"]["spread_position"][1] == pytest.approx(0.0508)
    # each number has Try values… for the station sweeps
    page.click("#test-setup [data-try='station-feeder-interval']")
    page.wait_for_selector("#test-sweep-dialog[open]")
    assert page.evaluate("__scrimmageTest.sweepFor[0].target") == {"station": "feeder", "key": "interval"}
    page.click("#test-sweep-dialog [data-close]")
    # the release box is tried in inches too (meters in the request)
    page.click("#test-setup [data-try='station-feeder-spread_position']")
    page.wait_for_selector("#test-sweep-dialog[open]")
    assert "(now 2 in)" in page.inner_text("#test-sweep-which")
    sent = []
    page.on("request", lambda r: sent.append(r.post_data_json) if r.url.endswith("/api/test/sweep") else None)
    page.fill("#test-sweep-list", "1, 3")
    page.click("#test-sweep-go")
    page.wait_for_function("__scrimmageTest.state.sweeps.length === 1 && __scrimmageTest.state.running === null",
                           timeout=120000)
    assert sent[0]["target"] == {"station": "feeder", "key": "spread_position"}
    assert sent[0]["values"] == [0.0254, 0.0762]
    page.wait_for_selector("#test-sweep-wrap", state="visible")
    assert page.inner_text("#test-sweep-title") == "Sweep 1: feeder: release box (± in)"
    # the table shows the values in inches, as they were typed
    assert page.eval_on_selector_all("#test-sweep-table .test-sweep-value", "bs => bs.map(b => b.textContent)") == ["1", "3"]


def _just_a_wait(text):
    """The station example's test with its feed blocks swapped for one wait block (its [setup] kept)."""
    return text[:text.index("[[block]]")] + '[[block]]\ndo = "wait"\nseconds = 0.5\n'


def test_settings_for_another_field_can_be_cleared_so_the_test_runs(test_page, tmp_path):
    """Moved to the ladder, the station test's station settings are left over: Setup lists them with Clear, and once
    cleared the test runs. The shuffle is no problem on a field without stations."""
    path = _example(tmp_path, "station", "feed.test.toml", _just_a_wait)
    page, _ = test_page(path=path)
    _open_setup(page)
    assert page.query_selector("#test-setup .test-setup-leftover") is None       # the station field uses them all
    robot = path.parent / "hopper-standin.toml"
    page.select_option("#test-field", "ladder")
    _wait_file(lambda: _toml(robot)["range"]["file"] == "ladder", robot)
    page.wait_for_function("__scrimmageTest.state.problems.length > 0")
    assert 'Setup names a station this field doesn\'t have: "feeder"' in page.inner_text("#test-problems")
    assert page.is_disabled("#test-run")
    page.wait_for_selector("#test-setup .test-setup-leftover")
    assert page.inner_text("#test-setup .test-setup-leftover legend") == "Settings for another field"
    row = "#test-setup .test-setup-leftover [data-leftover='station-feeder']"
    assert 'Station "feeder": this field has no station by that name' in page.inner_text(row)
    page.click(f"{row} button")
    _wait_file(lambda: "station" not in _toml(path).get("setup", {}), path)
    assert _toml(path)["setup"] == {"seed": 1}                                      # the shuffle is left alone
    page.wait_for_function("__scrimmageTest.state.problems.length === 0")
    page.wait_for_function("!document.querySelector('#test-run').disabled")
    assert page.query_selector("#test-setup .test-setup-leftover") is None
    assert _run(page, "001")["status"] == "finished"


def test_a_run_on_a_station_field_that_fed_nothing_has_no_fed_pieces_line(test_page, tmp_path):
    page, path = test_page(path=_example(tmp_path, "station", "feed.test.toml", _just_a_wait))
    run = _run(page, "001")
    assert run["status"] == "finished"
    result = json.loads((path.parent / "feed-runs" / "001" / "result.json").read_text(encoding="utf-8"))
    assert result["counts"] == {"fed": 0, "in": 0, "stuck": 0, "missed": 0}      # counted, but nothing to say
    assert page.is_hidden("#test-feeds-summary")


def test_a_cleared_hopper_stays_cleared_after_a_reload(test_page, tmp_path):
    path = _example(tmp_path, "station", "feed.test.toml")
    page, _ = test_page(path=path)
    _open_setup(page)
    page.uncheck("#test-setup .test-setup-hopper input[data-body='hopper.bin']")
    _wait_file(lambda: _toml(path)["test"].get("hopper") == [], path)
    page.reload()
    page.wait_for_function(READY)
    _open_setup(page)
    assert not page.is_checked("#test-setup .test-setup-hopper input[data-body='hopper.bin']")
    page.click("#test-new-shuffle")                     # the next Setup change doesn't bring the preselection back
    _wait_file(lambda: _toml(path)["setup"]["seed"] != 1, path)
    assert _toml(path)["test"]["hopper"] == []


def test_a_hook_change_saves_the_preselected_hopper_too(test_page, tmp_path):
    path = _example(tmp_path, "station", "feed.test.toml", lambda s: s.replace('hopper = ["hopper.bin"]\n', ""))
    page, _ = test_page(path=path)
    _open_setup(page)
    page.check("#test-setup .test-setup-hooks input[data-body='hopper.bin']")
    _wait_file(lambda: _toml(path)["test"].get("hooks") == ["hopper.bin"], path)
    assert _toml(path)["test"]["hopper"] == ["hopper.bin"]


def test_new_shuffle_changes_the_seed_in_the_file(test_page, tmp_path):
    path = _example(tmp_path, "station", "feed.test.toml")
    page, _ = test_page(path=path)
    _open_setup(page)
    assert page.inner_text("#test-seed") == "Shuffle 1"
    page.click("#test-new-shuffle")
    _wait_file(lambda: _toml(path)["setup"]["seed"] != 1, path)
    seed = _toml(path)["setup"]["seed"]
    assert 1 <= seed <= 999999
    page.wait_for_function(f"document.querySelector('#test-seed').textContent === 'Shuffle {seed}'")
    page.click("#test-setup [data-try='seed']")
    page.wait_for_selector("#test-sweep-dialog[open]")
    assert page.evaluate("__scrimmageTest.sweepFor[0].target") == {"setup": "seed"}
    assert page.input_value("#test-sweep-from") == str(seed)        # whole seeds, from this one on
    page.click("#test-sweep-dialog [data-close]")


def test_the_floor_spread_counts_runs_and_refuses_more_than_25(test_page, tmp_path):
    path = _example(tmp_path, "intake", "spread.test.toml")
    page, _ = test_page(path=path)
    _open_setup(page)
    page.wait_for_selector("#test-spread-count")
    assert page.inner_text("#test-spread-count") == "3 runs"
    # a ticked distance starts around the field's ball (59 in ahead), never inside the robot
    ball = page.evaluate("__scrimmageTest.state.palette.floor_ball")
    assert page.input_value("#test-spread-distance_in-from") == "47" and page.input_value("#test-spread-distance_in-to") == "71"
    assert ball["nearest_in"] <= 47
    page.fill("#test-spread-distance_in-from", "12")
    page.check("#test-spread-distance_in-on")
    page.wait_for_function("document.querySelector('#test-spread-count').textContent.includes('inside the robot')")
    assert page.inner_text("#test-spread-count") == (f"Distance ahead (in): {ball['nearest_in']} in or more"
                                                     " (nearer, the ball starts inside the robot)")
    page.uncheck("#test-spread-distance_in-on")
    page.wait_for_function("document.querySelector('#test-spread-count').textContent === '3 runs'")
    assert "distance_in" not in _toml(path)["setup"]["floor_spread"]
    assert page.is_checked("#test-spread-offset_in-on") and not page.is_checked("#test-spread-distance_in-on")
    page.check("#test-spread-angle_deg-on")
    page.fill("#test-spread-angle_deg-from", "-20")
    page.fill("#test-spread-angle_deg-to", "20")
    page.fill("#test-spread-angle_deg-steps", "3")
    page.press("#test-spread-angle_deg-steps", "Enter")
    page.wait_for_function("document.querySelector('#test-spread-count').textContent === '9 runs'")
    _wait_file(lambda: _toml(path)["setup"]["floor_spread"].get("angle_deg") == [-20.0, 0.0, 20.0], path)
    assert _toml(path)["setup"]["floor_spread"]["offset_in"] == [-6.0, 0.0, 6.0]
    assert page.is_enabled("#test-spread-run")
    page.fill("#test-spread-angle_deg-steps", "9")
    page.press("#test-spread-angle_deg-steps", "Enter")
    page.wait_for_function("document.querySelector('#test-spread-count').textContent.includes('at most 25 runs')")
    assert page.inner_text("#test-spread-count") == "27 runs: at most 25 runs; use fewer steps"
    assert page.is_disabled("#test-spread-run")
    page.evaluate("__scrimmageTest.saved")
    assert _toml(path)["setup"]["floor_spread"]["angle_deg"] == [-20.0, 0.0, 20.0]     # not written
    page.uncheck("#test-spread-angle_deg-on")
    page.wait_for_function("document.querySelector('#test-spread-count').textContent === '3 runs'")
    _wait_file(lambda: "angle_deg" not in _toml(path)["setup"]["floor_spread"], path)


def test_add_the_station_blocks_appends_them(test_page, tmp_path):
    path = _example(tmp_path, "station", "feed.test.toml",
                    lambda s: s.split("[[block]]")[0] + '[[block]]\ndo = "wait"\nseconds = 0.5\n')
    page, _ = test_page(path=path)
    _open_setup(page)
    page.click("#test-add-station-blocks")
    _wait_file(lambda: len(_file_blocks(path)) == 4, path)
    assert [b["do"] for b in _file_blocks(path)] == ["wait", "start_feeding", "wait_until", "stop_feeding"]
    assert _types(page)[0] == "wait"
    page.wait_for_function("!document.querySelector('#test-add-station-blocks')")     # a feed block is there now


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_station_setup_meets_wcag_aa(test_page, tmp_path, scheme):
    page, _ = test_page(path=_example(tmp_path, "station", "feed.test.toml"), scheme=scheme)
    _open_setup(page)
    page.wait_for_selector("#test-station-feeder-interval")
    page.wait_for_timeout(200)
    assert page.locator("#test-setup input").count() > 15
    assert page.evaluate(f"({CONTRAST_JS}).check(document.querySelector('#test-setup'))") == []


# A station field of our own that feeds 3 pieces a second apart onto the stand-in's bin with a shelf on its back wall
# (tests/test_blocks_feeds.py): the first lodges on the shelf (stuck), the second rolls off to the floor (missed), the
# third lands in the bin (in).
FEED_3 = '\n[[block]]\ndo = "start_feeding"\nstation = "feeder"\n\n[[block]]\ndo = "wait"\nseconds = 3.0\n'


def _fed_test(tmp_path, extra="stock = 3\ninterval = 1.0\n", blocks=FEED_3):
    from test_blocks_feeds import HOPPER, SHELF, station_file, write as write_fed
    folder = tmp_path / "fed"
    folder.mkdir()
    field = station_file(folder, (1.523, 4.0, 1.0), extra=extra)
    return write_fed(folder, blocks, field, HOPPER + SHELF)


def _result(page, run_id):
    return page.evaluate(f"""import('/app.js').then(app => app.api('GET', '/api/test/run/{run_id}'))
                         .then(r => r.data)""")


def test_a_fed_run_marks_every_piece_and_jumps_to_a_stuck_one(test_page, tmp_path):
    page, _ = test_page(path=_fed_test(tmp_path))
    _run(page, "001")
    res = _result(page, "001")
    feeds, counts = res["feeds"], res["counts"]
    assert counts == {"fed": 3, "in": 1, "stuck": 1, "missed": 1}, res["outcome"]
    assert page.evaluate("__scrimmageTest.replay.feeds") == feeds
    marks = page.locator("#test-feed-marks .test-feed-mark")
    assert marks.count() == counts["fed"]
    assert [marks.nth(k).get_attribute("data-outcome") for k in range(3)] == [f["outcome"] for f in feeds]
    missed = next(k for k, f in enumerate(feeds) if f["outcome"] == "missed")
    f = feeds[missed]
    assert marks.nth(missed).get_attribute("title") == (f"piece {missed + 1} from feeder at {f['t']:.1f} s: missed"
                                                         f" ({f['why']})")
    assert page.inner_text("#test-feeds-summary").startswith("Fed pieces: 1 in · 1 stuck · 1 missed")
    # the stuck piece's button jumps the replay to when it was stuck and outlines that ball
    stuck = next(f for f in feeds if f["outcome"] == "stuck")
    button = page.locator("#test-feeds-summary button")
    assert button.count() == 1 and button.inner_text() == f"Show the stuck piece (at {stuck['t_outcome']:.1f} s)"
    button.click()
    assert page.evaluate("__scrimmageTest.replay.time") == pytest.approx(stuck["t_outcome"], abs=1 / 30)
    assert page.evaluate("__scrimmageTest.replay.outlined") == stuck["ball"]
    # a click on a mark jumps to that piece's outcome
    marks.nth(missed).click()
    assert page.evaluate("__scrimmageTest.replay.time") == pytest.approx(f["t_outcome"], abs=1 / 30)
    assert page.evaluate("__scrimmageTest.replay.outlined") == f["ball"]


def test_a_stuck_pieces_mark_says_what_it_rests_on(test_page, tmp_path):
    page, _ = test_page(path=_fed_test(tmp_path))
    _run(page, "001")
    stuck = next(f for f in _result(page, "001")["feeds"] if f["outcome"] == "stuck")
    title = page.get_attribute(f"#test-feed-marks [data-ball='{stuck['ball']}']", "title")
    assert title.endswith(f"stuck in the robot (resting on {stuck['body']})"), title


def test_many_fed_pieces_shrink_their_marks_to_stay_on_the_scrubber_and_clickable(test_page, tmp_path):
    page, _ = test_page(path=_fed_test(tmp_path, "stock = 60\ninterval = 0.15\n", FEED_3.replace("3.0", "8.0")))
    _run(page, "001")
    n = page.locator("#test-feed-marks .test-feed-mark").count()
    assert n >= 30, n
    boxes = page.evaluate("""(() => {
      const wrap = document.querySelector('.test-scrub-wrap').getBoundingClientRect();
      const marks = [...document.querySelectorAll('#test-feed-marks .test-feed-mark')];
      return {wrap: [wrap.left, wrap.right], marks: marks.map((m) => {
        const r = m.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return {left: r.left, right: r.right, width: m.offsetWidth, hit: hit === m};
      })};
    })()""")
    assert all(b["width"] >= 4 for b in boxes["marks"]) and all(b["width"] < 11 for b in boxes["marks"])
    assert all(boxes["wrap"][0] - 1 <= b["left"] and b["right"] <= boxes["wrap"][1] + 1 for b in boxes["marks"])
    assert all(b["hit"] for b in boxes["marks"]), [(round(b["left"]), b["width"], b["hit"]) for b in boxes["marks"]]


def test_a_seed_sweeps_title_says_the_shuffle(test_page, tmp_path):
    page, _ = test_page(path=_example(tmp_path, "station", "feed.test.toml", _just_a_wait))
    _open_setup(page)
    page.click("#test-setup [data-try='seed']")
    page.wait_for_selector("#test-sweep-dialog[open]")
    for sel, v in zip(("#test-sweep-from", "#test-sweep-to", "#test-sweep-steps"), ("1", "2", "2")):
        page.fill(sel, v)
    page.click("#test-sweep-go")
    page.wait_for_function("!document.querySelector('#test-sweep-dialog').open")
    page.wait_for_function("__scrimmageTest.sweep && __scrimmageTest.state.running === null"
                           " && __scrimmageTest.sweep.rows === 2", timeout=120000)
    assert page.inner_text("#test-sweep-title") == "Sweep 1: the shuffle"


def test_a_spread_shows_its_sentence_and_a_row_replays_its_run(test_page, tmp_path):
    page, _ = test_page(path=_example(tmp_path, "intake", "spread.test.toml"))
    _open_setup(page)
    page.wait_for_selector("#test-spread-run:not([disabled])")
    page.click("#test-spread-run")
    page.wait_for_function("__scrimmageTest.state.spreads.length === 1 && __scrimmageTest.state.running === null"
                           " && __scrimmageTest.replay.runId === 'spread-001/003'", timeout=120000)
    spread = page.evaluate("__scrimmageTest.state.spreads[0]")
    page.wait_for_selector("#test-spread-wrap", state="visible")
    assert page.inner_text("#test-spread-title") == "Spread 1"
    assert page.inner_text("#test-spread-sentence") == _sentence(spread["sentence"])
    rows = page.locator("#test-spread-table tbody tr")
    assert rows.count() == 3
    assert [rows.nth(k).locator("td").first.inner_text() for k in range(3)] == [r["placement"] for r in spread["rows"]]
    assert [rows.nth(k).locator("td").nth(1).inner_text() for k in range(3)] == [r["picked up"] for r in spread["rows"]]
    status = [r["status"] for r in spread["rows"]]
    assert page.inner_text("#test-result-title") == f"Spread 1, placement 3 · {status[2]}"
    page.click("#test-spread-table [data-run='spread-001/001']")
    page.wait_for_function("__scrimmageTest.replay.runId === 'spread-001/001'")
    assert page.inner_text("#test-result-title") == f"Spread 1, placement 1 · {status[0]}"
    assert rows.nth(0).get_attribute("aria-current") == "true"
    assert "Spread 1, placement 1" in page.inner_text("#test-runs")
    # a floor run marks its one piece, from the floor
    assert page.locator("#test-feed-marks .test-feed-mark").count() == 1
    assert " from the floor at " in page.get_attribute("#test-feed-marks .test-feed-mark", "title")
    _run(page, "001")
    assert page.is_hidden("#test-spread-wrap")


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_feed_marks_are_3_to_1_on_their_background(test_page, tmp_path, scheme):
    """WCAG 1.4.11 for the feed marks (shapes with no text): each outcome's color against the panel behind them; and
    the run column with them shown meets AA."""
    page, _ = test_page(path=_fed_test(tmp_path), scheme=scheme)
    _run(page, "001")
    colors = page.evaluate("__scrimmageTest.replay.feedColors")
    assert set(colors) == {"in", "stuck", "missed", "bg"}
    for k in ("in", "stuck", "missed"):
        assert _hex_ratio(colors[k], colors["bg"]) >= 3, (k, colors)
    assert page.evaluate(f"({CONTRAST_JS}).check(document.querySelector('#test-run-col'))") == []
