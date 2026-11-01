"""Browser end-to-end tests of the Drive tab (headless Chromium): Driver Station bar, sim controls, live view and
readouts against a real `scrimmage edit` driving tests/fake_robot_main.py (`--drive`: both sides follow port 0's
axis 1, pushed forward = -1, while enabled, PWM 1 negated to match the config's inverted right side, so it drives
straight).
"""
import os
import re

import pytest

from test_assembler_browser import REPO, Server, browser, scenario_page  # noqa: F401  (fixtures)
from test_live import config_copy

pytestmark = pytest.mark.browser

# One standard-mapping gamepad with its left stick pushed fully forward (axis 1 = -1).
VIRTUAL_PAD = """
(() => {
  const pad = { index: 0, id: 'Virtual pad (STANDARD GAMEPAD)', connected: true, mapping: 'standard', timestamp: 1,
                axes: [0, -1, 0, 0], buttons: Array.from({ length: 17 }, () => ({ pressed: false, touched: false, value: 0 })) };
  navigator.getGamepads = () => [pad, null, null, null];
})();
"""


@pytest.fixture
def drive_page(browser, tmp_path, monkeypatch):
    """drive_page(config=None, init_script=None) -> page on the Drive tab; the config defaults to a copy of
    tests/fake_robot.toml."""
    monkeypatch.setenv("SCRIMMAGE_NT_CONNECT_TIMEOUT", "0.5")   # the fake robot serves no NetworkTables
    servers, contexts, logs = [], [], []

    def make(config=None, init_script=None):
        cfg = config_copy(tmp_path) if config is None else config
        srv = Server(cfg, cwd=tmp_path)
        servers.append(srv)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        contexts.append(ctx)
        if init_script:
            ctx.add_init_script(init_script)
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        page.on("console", lambda m: logs.append(f"console.{m.type}: {m.text} ({m.location.get('url') or '?'})") if m.type == "error" else None)
        page.goto(srv.url)
        page.wait_for_function("window.__scrimmage && __scrimmage.state && __scrimmage.live")
        page.click("#tab-btn-drive")
        page.wait_for_selector("#tab-drive:not([hidden])")
        page.wait_for_function("__scrimmage.live && __scrimmage.live.connected")
        return page

    yield make
    for c in contexts:
        c.close()
    for s in servers:
        s.stop()
    assert not logs, f"browser errors: {logs}"


def start(page):
    page.click("#drive-start")
    page.wait_for_function("__scrimmage.live.state && __scrimmage.live.state.state === 'running' "
                           "&& __scrimmage.live.state.readouts", timeout=60000)


def x_now(page):
    return page.evaluate("__scrimmage.live.state.readouts.pose.x")


def stop(page):
    page.click("#drive-stop")
    page.wait_for_function("__scrimmage.live.state.state === 'stopped'", timeout=30000)


def test_drive_with_the_keyboard_pause_step_speed(drive_page):
    page = drive_page()
    assert page.evaluate("__scrimmage.tab") == "drive"
    assert page.is_disabled("#undo") and page.is_disabled("#redo")
    assert page.input_value("#drive-port-0") == "keyboard"   # no gamepad: the keyboard drives port 0
    start(page)
    page.wait_for_function("__scrimmage.live.frames > 0 && __scrimmage.live.lastFrame.poses.length > 0")
    page.click("[data-key='mode-teleop']")
    page.click("#drive-enable")
    page.wait_for_function("__scrimmage.live.state.enabled && __scrimmage.live.state.mode === 'teleop'")
    assert "Enabled" in page.text_content("#drive-hud")

    # typing in a text box doesn't drive the robot
    page.click("#drive-signal-0")
    x0 = x_now(page)
    page.keyboard.down("w")
    page.wait_for_timeout(600)
    page.keyboard.up("w")
    page.wait_for_timeout(300)
    assert abs(x_now(page) - x0) < 0.01
    page.fill("#drive-signal-0", "")

    page.click("#drive-hud")   # focus out of the text box
    x0 = x_now(page)
    page.keyboard.down("w")
    page.wait_for_timeout(1000)
    page.keyboard.up("w")
    page.wait_for_timeout(200)
    assert x_now(page) - x0 > 0.05

    page.click("#drive-pause")
    page.wait_for_function("__scrimmage.live.state.paused && __scrimmage.live.state.state === 'paused'")
    t0 = page.evaluate("__scrimmage.live.state.t")
    page.wait_for_timeout(500)
    assert page.evaluate("__scrimmage.live.state.t") == t0
    page.click("#drive-step")
    page.wait_for_function(f"__scrimmage.live.state.t !== {t0}")
    assert page.evaluate("__scrimmage.live.state.t") - t0 == pytest.approx(0.005, abs=1e-9)
    assert page.evaluate("__scrimmage.live.state.paused")
    page.click("#drive-pause")   # Resume
    page.wait_for_function("!__scrimmage.live.state.paused")

    page.select_option("#drive-speed", "2")
    page.wait_for_function("__scrimmage.live.state.speed === 2")
    page.select_option("#drive-speed", "max")
    page.wait_for_function("__scrimmage.live.state.speed === null")
    page.select_option("#drive-speed", "1")   # back to real time: "max" takes every CPU a slow machine has
    page.wait_for_function("__scrimmage.live.state.speed === 1")

    page.keyboard.press("Space")   # focus is on the Resume button: Space still disables
    page.wait_for_function("!__scrimmage.live.state.enabled && __scrimmage.live.state.mode === 'disabled'")
    assert not page.evaluate("__scrimmage.live.state.paused")
    page.click("#drive-enable")
    page.wait_for_function("__scrimmage.live.state.enabled")
    page.keyboard.press("Space")   # right after clicking Enable, focus still on it
    page.wait_for_function("!__scrimmage.live.state.enabled")
    page.click("#drive-hud")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.live.state.enabled")
    page.keyboard.press("Space")
    page.wait_for_function("!__scrimmage.live.state.enabled")

    # readouts and the console
    assert re.search(r"x\s*-?\d", page.text_content("#drive-readouts"))
    assert page.locator("#drive-readouts .motor-row").count() == 2
    page.wait_for_function("document.querySelector('#drive-console').textContent.length > 0")
    stop(page)


def test_a_signal_picker_plots_the_signal(drive_page):
    page = drive_page()
    start(page)
    page.fill("#drive-signal-0", "battery")
    page.press("#drive-signal-0", "Tab")
    page.wait_for_function("__scrimmage.live.state.signals && typeof __scrimmage.live.state.signals.battery === 'number'")
    page.wait_for_function("__scrimmage.live.plots[0] > 2")   # samples in the first plot
    stop(page)


def test_a_virtual_gamepad_on_port_0_drives(drive_page):
    page = drive_page(init_script=VIRTUAL_PAD)
    assert page.input_value("#drive-port-0") == "0"   # the first connected gamepad goes on port 0
    page.select_option("#drive-port-0", "keyboard")
    page.select_option("#drive-port-0", "0")
    start(page)
    page.click("[data-key='mode-teleop']")
    x0 = x_now(page)
    page.click("#drive-hud")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.live.state.enabled")
    page.wait_for_function(f"__scrimmage.live.state.readouts.pose.x - {x0} > 0.05", timeout=10000)
    page.keyboard.press("Space")
    page.wait_for_function("!__scrimmage.live.state.enabled")
    stop(page)


def test_a_config_without_a_robot_program_shows_why(drive_page, tmp_path):
    text = (REPO / "configs" / "kitbot.toml").read_text()
    head, _, tail = text.partition("[robot_program]")
    cfg = tmp_path / "kitbot.toml"
    cfg.write_text(head + "[telemetry]" + tail.partition("[telemetry]")[2])
    page = drive_page(cfg)
    # Start is disabled with the reason before anyone presses it
    page.wait_for_selector("#drive-banner:not([hidden])")
    assert "[robot_program]" in page.text_content("#drive-banner")
    assert page.is_disabled("#drive-start")
    # fixed on disk: the banner goes and Start comes back
    cfg.write_text(text)
    page.wait_for_selector("#drive-banner", state="hidden")
    assert not page.is_disabled("#drive-start")
    cfg.write_text(head + "[telemetry]" + tail.partition("[telemetry]")[2])
    page.wait_for_selector("#drive-banner:not([hidden])")
    page.click("#drive-banner-robot")
    page.wait_for_selector("#tab-robot:not([hidden])")


def test_drive_this_robot_from_the_scenario_tab(scenario_page):
    page, _ = scenario_page("fake_pass.toml")
    page.click("#sc-drive")
    page.wait_for_selector("#tab-drive:not([hidden])")
    assert page.evaluate("__scrimmage.tab") == "drive"
    page.click("#tab-btn-scenario")
    page.wait_for_selector("#tab-scenario:not([hidden])")
    assert not page.is_disabled("#tab-btn-drive")


@pytest.mark.skipif(not os.environ.get("SCRIMMAGE_README_SHOT"),
                    reason="set SCRIMMAGE_README_SHOT=<png> to write the README screenshot (docs/img/drive.png)")
def test_readme_screenshot(drive_page, tmp_path):
    # the server runs in tmp_path with the config there, so the page shows just the file name, never a home path
    page = drive_page()
    start(page)
    page.wait_for_function("__scrimmage.live.frames > 0 && __scrimmage.live.lastFrame.poses.length > 0")
    page.click("[data-key='mode-teleop']")
    page.click("#drive-enable")
    page.wait_for_function("__scrimmage.live.state.enabled")
    page.click("#drive-hud")   # focus out of any text box
    page.keyboard.down("w")
    page.wait_for_timeout(1000)
    page.wait_for_function("__scrimmage.live.state.readouts.pose.x > 0.02")
    page.wait_for_timeout(300)
    shown = page.inner_text("body")   # what the picture can show (title tooltips never appear in it)
    for bad in (str(tmp_path), str(REPO)):
        i = shown.find(bad)
        assert i < 0, shown[max(0, i - 120):i + 120]
    assert "/home/" + os.environ.get("USER", "-") not in shown
    page.screenshot(path=os.environ["SCRIMMAGE_README_SHOT"])
    page.keyboard.up("w")
    stop(page)


def test_a_reload_shows_the_servers_chosen_mode(drive_page):
    page = drive_page()
    start(page)
    page.click("[data-key='mode-auto']")
    page.wait_for_function("__scrimmage.live.state.chosen_mode === 'auto'")
    page.reload()
    page.wait_for_function("window.__scrimmage && __scrimmage.state && __scrimmage.live")
    page.click("#tab-btn-drive")
    page.wait_for_function("__scrimmage.live.connected && __scrimmage.live.state && __scrimmage.live.state.state === 'paused'")
    page.wait_for_timeout(300)
    assert page.get_attribute("[data-key='mode-auto']", "aria-pressed") == "true"
    assert page.get_attribute("[data-key='mode-teleop']", "aria-pressed") == "false"
    page.click("#drive-enable")
    page.wait_for_function("__scrimmage.live.state.enabled")
    assert page.evaluate("__scrimmage.live.state.mode") == "auto"
    page.click("#drive-disable")
    stop(page)


def test_drive_tab_selects_dont_take_the_driving_keys(drive_page):
    page = drive_page()
    start(page)
    page.click("[data-key='mode-teleop']")
    page.click("#drive-enable")
    page.wait_for_function("__scrimmage.live.state.enabled && __scrimmage.live.state.mode === 'teleop'")
    page.focus("#drive-camera")
    page.select_option("#drive-camera", "top")   # a pick leaves the keys to the robot
    x0 = x_now(page)
    page.keyboard.down("w")
    page.wait_for_timeout(1000)
    page.keyboard.up("w")
    page.wait_for_timeout(200)
    assert x_now(page) - x0 > 0.05
    page.keyboard.press("1")
    page.keyboard.press("2")
    assert page.input_value("#drive-camera") == "top" and page.input_value("#drive-speed") == "1"

    page.focus("#drive-speed")    # focused without a pick: the driving keys still go to the robot
    x0 = x_now(page)
    page.keyboard.down("w")
    page.wait_for_timeout(1000)
    page.keyboard.up("w")
    page.wait_for_timeout(200)
    assert x_now(page) - x0 > 0.05
    page.keyboard.press("2")      # button 2, not 2x
    page.wait_for_timeout(300)
    assert page.evaluate("__scrimmage.live.state.speed") == 1 and page.input_value("#drive-speed") == "1"
    page.keyboard.press("m")      # not a driving key: the list's own type-ahead picks max
    page.wait_for_function("__scrimmage.live.state.speed === null")
    page.keyboard.press("Space")
    page.wait_for_function("!__scrimmage.live.state.enabled")
    stop(page)


# No gamepad at load (browsers expose one only after a button press); __plugPad() connects one.
LATE_PAD = """
(() => {
  const pads = [null, null, null, null];
  navigator.getGamepads = () => pads;
  window.__plugPad = () => {
    pads[0] = { index: 0, id: 'Late pad (STANDARD GAMEPAD)', connected: true, mapping: 'standard', timestamp: 1,
                axes: [0, -1, 0, 0], buttons: Array.from({ length: 17 }, () => ({ pressed: false, touched: false, value: 0 })) };
    const e = new Event('gamepadconnected');
    e.gamepad = pads[0];
    window.dispatchEvent(e);
  };
})();
"""


def test_a_gamepad_connected_after_load_takes_port_0(drive_page):
    page = drive_page(init_script=LATE_PAD)
    assert page.input_value("#drive-port-0") == "keyboard"
    page.evaluate("__plugPad()")
    page.wait_for_function("document.querySelector('#drive-port-0').value === '0'")
    assert page.evaluate("__scrimmage.live.assignments") == {"0": 0}
    assert "port 0" in page.text_content("#notices")

    # a port 0 the student set stays as they set it
    page = drive_page(init_script=LATE_PAD)
    page.select_option("#drive-port-0", "")
    page.select_option("#drive-port-0", "keyboard")
    page.evaluate("__plugPad()")
    page.wait_for_function("[...document.querySelector('#drive-port-0').options].some((o) => o.value === '0')")
    assert page.input_value("#drive-port-0") == "keyboard"
    assert page.evaluate("__scrimmage.live.assignments") == {"0": "keyboard"}
