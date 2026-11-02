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
    """open(blocks, scheme="light") -> (page, the test file); everything is torn down afterwards."""
    monkeypatch.setenv("SCRIMMAGE_SKIP_JAVA", "1")
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    servers, contexts, logs = [], [], []

    def open_(blocks=SPIN, scheme="light"):
        path = _write(tmp_path, blocks)
        srv = Server(path, cwd=tmp_path)
        servers.append(srv)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950}, color_scheme=scheme)
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
