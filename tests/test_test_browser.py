"""Browser tests of the Test tab's block editor (`scrimmage edit t.test.toml`), headless Chromium through Playwright.

The fixtures (Chromium lookup, the Server helper) live in tests/test_assembler_browser.py. The robot is the stand-in
box with the stock arm (motor and joint arm.pivot), as in tests/test_test_server.py.
"""
import tomllib

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
    """open(blocks, scheme="light", viewport=(1500, 950)) -> (page, the test file); everything is torn down
    afterwards."""
    monkeypatch.setenv("SCRIMMAGE_SKIP_JAVA", "1")
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    servers, contexts, logs = [], [], []

    def open_(blocks=SPIN, scheme="light", viewport=(1500, 950)):
        path = _write(tmp_path, blocks)
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
    assert len(st["blocks"]) == 10
    assert page.evaluate("__scrimmageTest.toJSON()") == st
    # the condition blocks sit in their sockets
    types = page.evaluate("__scrimmageTest.workspace.getAllBlocks(false).map(b => b.type)")
    for kind in ("cond_tilt", "cond_joint", "cond_touch", "cond_piece_in", "cond_hanging_from", "cond_level"):
        assert kind in types, kind


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
    page.evaluate(f"""(() => {{
      __scrimmageTest.workspace.getTopBlocks(true)[0].getNextBlock().setFieldValue(3, 'SECONDS');
    }})()""")
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
    page.wait_for_function("document.querySelector('#test-count').textContent === '13 blocks in the test'")


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
    page.check("#test-setup input[data-body='arm.arm']")
    page.evaluate("__scrimmageTest.saved")
    _wait_file(lambda: tomllib.loads(path.read_text(encoding="utf-8"))["test"].get("hooks") == ["arm.arm"], path)
    page.wait_for_function("__scrimmageTest.state.palette.hooks.includes('arm.arm')")
    assert "hooks: arm.arm" in page.inner_text("#test-setup summary")
    # the hooks offered are the mechanism's: not the drivetrain's or the chassis's
    assert page.locator("#test-setup input[data-body='dt.left_1']").count() == 0
    assert page.locator("#test-setup input[data-body='drive.chassis']").count() == 0
    assert page.locator("#test-setup input[data-body='arm.base']").count() == 1
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
