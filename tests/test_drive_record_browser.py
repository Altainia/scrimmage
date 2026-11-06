"""Browser end-to-end tests of the Drive tab's poke tools and recording, and of what the Scenario tab does with a
recording: the track plot (trim), running it and watching the run live. The robot is tests/fake_robot_main.py
(`--drive`: both sides follow port 0's axis 1, pushed forward = -1, while enabled, PWM 1 negated to match the
config's inverted right side, so it drives straight) through
test_drive_browser.py's `drive_page` fixture.
"""
import re

import pytest

from test_assembler_browser import browser  # noqa: F401  (fixture)
from test_drive_browser import drive_page, start, stop  # noqa: F401  (fixture)
from test_live import TESTS, config_copy

pytestmark = pytest.mark.browser

START = (2.0, 4.035)   # tests/fake_robot.toml's start pose


def pose(page):
    return page.evaluate("__scrimmage.live.state.readouts.pose")


def wait_pose_near(page, x, y, tol=0.1):
    page.wait_for_function(f"(() => {{ const p = __scrimmage.live.state.readouts.pose; "
                           f"return Math.abs(p.x - {x}) < {tol} && Math.abs(p.y - {y}) < {tol}; }})()")


def field_point(page, x, y):
    return page.evaluate(f"__scrimmage.miniField.toScreen({x}, {y})")


def drag_on_field(page, a, b):
    p, q = field_point(page, *a), field_point(page, *b)
    page.mouse.move(p["x"], p["y"])
    page.mouse.down()
    page.mouse.move(q["x"], q["y"], steps=6)
    page.mouse.up()


def test_set_pose_on_the_mini_field_push_and_reset(drive_page):
    page = drive_page()
    start(page)
    page.wait_for_selector("#drive-pokes canvas")
    page.wait_for_function("__scrimmage.miniField && __scrimmage.live.frames > 0")

    # drag from (5, 3) toward +y: the robot goes there, facing 90°
    drag_on_field(page, (5, 3), (5, 4))
    wait_pose_near(page, 5, 3)
    assert pose(page)["heading_deg"] == pytest.approx(90, abs=3)

    # a click keeps the current heading: the one the mini-field shows, from the frames (a state message with the
    # new pose can arrive before the next frame does)
    page.wait_for_function("(() => { const r = __scrimmage.miniField.robot; "
                           "return r && Math.abs(r.heading - 90) < 3; })()")
    p = field_point(page, 6, 3)
    page.mouse.click(p["x"], p["y"])
    wait_pose_near(page, 6, 3)
    assert pose(page)["heading_deg"] == pytest.approx(90, abs=3)

    # push it along +y
    y0 = pose(page)["y"]
    page.fill("#push-force", "400")
    page.fill("#push-dir", "90")
    page.fill("#push-duration", "0.5")
    page.click("#push-go")
    page.wait_for_function(f"__scrimmage.live.state.readouts.pose.y - {y0} > 0.05", timeout=10000)

    # Reset puts it back at its start pose
    page.click("#drive-reset")
    wait_pose_near(page, *START)
    stop(page)


def ball_config(tmp_path):
    """A copy of tests/fake_robot.toml with a free-jointed ball (radius 0.25 m) at (5, 5) added to the kitbot world."""
    xml = (TESTS / "models" / "kitbot.xml").read_text()
    assert "</worldbody>" in xml
    ball = '<body name="ball" pos="5 5 0.25"><freejoint/><geom type="sphere" size="0.25" mass="0.5"/></body>'
    (tmp_path / "ball.xml").write_text(xml.replace("</worldbody>", ball + "</worldbody>", 1))
    cfg = config_copy(tmp_path)
    text = cfg.read_text()
    old = f'mjcf = "{TESTS / "models" / "kitbot.xml"}"'
    assert old in text
    cfg.write_text(text.replace(old, f'mjcf = "{tmp_path / "ball.xml"}"'))
    return cfg


def test_drag_a_free_body_but_not_a_wall(drive_page, tmp_path):
    page = drive_page(ball_config(tmp_path))
    start(page)
    page.wait_for_function("__scrimmage.miniField && __scrimmage.miniField.objects.length > 0")
    # only the free body is draggable: the named walls and the obstacle stay part of the field
    assert [o["name"] for o in page.evaluate("__scrimmage.miniField.objects")] == ["ball"]
    ball = page.evaluate("__scrimmage.miniField.objects[0]")
    assert (ball["x"], ball["y"]) == (pytest.approx(5, abs=0.05), pytest.approx(5, abs=0.05))

    # drag the ball from a point off its center: it keeps that offset
    drag_on_field(page, (5.1, 5), (7.1, 3))
    page.wait_for_function("(() => { const b = __scrimmage.miniField.objects[0]; "
                           "return Math.abs(b.x - 7) < 0.1 && Math.abs(b.y - 3) < 0.1; })()")
    p = pose(page)
    assert (p["x"], p["y"]) == (pytest.approx(START[0], abs=0.1), pytest.approx(START[1], abs=0.1))   # the robot stayed

    # a drag starting on a wall sets the robot's pose (facing the drag) instead of moving the wall
    drag_on_field(page, (5, 8.12), (5, 7.12))
    page.wait_for_function("(() => { const p = __scrimmage.live.state.readouts.pose; "
                           "return Math.abs(p.x - 5) < 0.3 && p.y > 7; })()")
    assert pose(page)["heading_deg"] == pytest.approx(-90, abs=5)
    stop(page)


def test_record_save_trim_overwrite_run_and_watch(drive_page, tmp_path):
    page = drive_page()
    start(page)
    assert page.is_disabled("#drive-save")
    page.click("[data-key='mode-teleop']")
    page.click("#drive-enable")
    page.wait_for_function("__scrimmage.live.state.enabled && __scrimmage.live.state.mode === 'teleop'")

    page.click("#drive-record")
    page.wait_for_function("__scrimmage.live.state.recording")
    assert "● REC" in page.text_content("#drive-record")
    page.click("#drive-hud")   # focus away from the buttons, then drive
    page.keyboard.down("w")
    page.wait_for_timeout(1000)
    page.keyboard.up("w")
    page.click("[data-key='mode-auto']")
    page.wait_for_function("__scrimmage.live.state.mode === 'auto'")
    page.wait_for_timeout(300)
    page.click("#push-go")        # two pokes while recording
    page.click("#drive-reset")
    page.wait_for_function("__scrimmage.live.state.pokes === 2")
    page.wait_for_function("document.querySelector('#drive-record').textContent.includes('2')")
    page.click("#drive-disable")
    page.wait_for_function("!__scrimmage.live.state.enabled")
    page.wait_for_timeout(200)
    page.click("#drive-record")
    page.wait_for_function("!__scrimmage.live.state.recording")
    assert "REC" not in page.text_content("#drive-record")
    stop(page)   # one simulation at a time: the run below needs the drive session gone

    # Save as scenario
    page.wait_for_function("__scrimmage.live.state.can_save")
    page.click("#drive-save")
    page.wait_for_selector("#save-dialog[open]")
    assert re.fullmatch(r"scenarios/drive-\d{8}-\d{4}\.toml", page.input_value("#save-path"))
    assert "2 pokes happened while recording and are not in the scenario" in page.text_content("#save-dialog")
    page.fill("#save-path", "scenarios/rec.toml")
    page.click("#save-go")
    page.wait_for_function("__scrimmage.tab === 'scenario' && __scrimmage.scenario && __scrimmage.scenario.state "
                           "&& __scrimmage.scenario.state.path.endsWith('rec.toml')")
    assert (tmp_path / "scenarios" / "rec.toml").is_file()
    steps = page.evaluate("__scrimmage.scenario.state.steps")
    assert len(steps) >= 2 and all(s["inputs"] for s in steps)
    # the Scenario tab settled: its robot's signals loaded (loadRobotInfo), whose render rebuilds the track panel
    page.wait_for_function("__scrimmage.scenario.signals !== null")
    page.wait_for_selector("#track-canvas", state="visible")
    assert page.input_value("#trim-start") == "0"

    # Trim the first step's start; a re-render (here a language change) keeps what is typed, and the focus
    d0 = steps[0]["duration"]
    page.fill("#trim-start", "0.1")
    for lang in ("en-GB", "en-US"):
        page.evaluate("document.querySelector('#trim-start').dataset.old = '1'")
        page.evaluate("(tag) => import('./i18n.js').then((m) => m.setLanguage(tag))", lang)
        page.wait_for_selector("#trim-start:not([data-old])")
        assert page.input_value("#trim-start") == "0.1"
        assert page.evaluate("document.activeElement.id") == "trim-start"
    page.click("#trim-go")
    page.wait_for_function(f"__scrimmage.scenario.state.steps[0].duration < {d0} - 0.05")
    assert page.evaluate("__scrimmage.scenario.state.steps[0].duration") == pytest.approx(d0 - 0.1, abs=1e-6)
    assert page.evaluate("__scrimmage.scenario.state.steps[0].inputs") != steps[0]["inputs"]
    page.wait_for_selector("#track-canvas", state="visible")

    # Saving again to the same file asks before overwriting it
    page.click("#tab-btn-drive")
    page.click("#drive-save")
    page.wait_for_selector("#save-dialog[open]")
    page.fill("#save-path", "scenarios/rec.toml")
    page.click("#save-go")
    page.wait_for_selector("#save-overwrite", state="visible")
    assert "Overwrite?" in page.text_content("#save-dialog")
    page.click("#save-overwrite")
    page.wait_for_function(f"__scrimmage.tab === 'scenario' && Math.abs(__scrimmage.scenario.state.steps[0].duration - {d0}) < 1e-6")

    # Run it (no checks: it passes) and watch it live
    page.select_option("#run-speed", "0.5")
    n = page.evaluate("__scrimmage.scenario.runs.length")
    page.click("#run-btn")
    page.wait_for_function(f"__scrimmage.scenario.runs.length === {n + 1} && __scrimmage.scenario.runs[0].state === 'running'")
    page.wait_for_selector("#run-watch", state="visible")
    page.click("#run-watch")
    page.wait_for_selector("#run-watch-view", state="visible")
    page.wait_for_function("__scrimmage.live3d && __scrimmage.live3d.lastSeq >= 0", timeout=30000)
    seq = page.evaluate("__scrimmage.live3d.lastSeq")
    page.wait_for_function(f"__scrimmage.live3d.lastSeq > {seq}", timeout=30000)
    page.wait_for_function("__scrimmage.scenario.runs[0].state !== 'running'", timeout=60000)
    assert page.evaluate("__scrimmage.scenario.runs[0].state") == "passed"
    page.wait_for_selector("#run-watch-view", state="hidden")
    page.wait_for_selector("#run-watch", state="hidden")


def test_enter_and_space_leave_buttons_and_dialogs_alone(drive_page):
    page = drive_page()
    start(page)
    page.click("[data-key='mode-teleop']")
    page.click("#drive-record")
    page.wait_for_function("__scrimmage.live.state.recording")
    page.wait_for_timeout(300)
    page.click("#drive-record")
    page.wait_for_function("!__scrimmage.live.state.recording && __scrimmage.live.state.can_save")

    page.click("#drive-save")
    page.wait_for_selector("#save-dialog[open]")
    page.focus("#save-cancel")
    page.keyboard.press("Enter")      # presses Cancel, doesn't enable the robot
    page.wait_for_function("!document.querySelector('#save-dialog').open")
    page.wait_for_timeout(300)
    assert not page.evaluate("__scrimmage.live.state.enabled")

    page.focus("#drive-pause")
    page.keyboard.press("Enter")      # presses Pause
    page.wait_for_function("__scrimmage.live.state.paused")
    page.wait_for_timeout(300)
    assert not page.evaluate("__scrimmage.live.state.enabled")
    stop(page)


def test_space_disables_even_on_a_focused_button(drive_page):
    """Space is the e-stop: it disables whatever button has focus, and doesn't press that button."""
    page = drive_page()
    start(page)
    page.click("[data-key='mode-teleop']")
    page.click("#drive-enable")
    page.wait_for_function("__scrimmage.live.state.enabled")
    page.keyboard.press("Space")
    page.wait_for_function("!__scrimmage.live.state.enabled")

    page.click("#drive-hud")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.live.state.enabled")
    page.click("#drive-record")
    page.wait_for_function("__scrimmage.live.state.recording")
    page.keyboard.press("Space")
    page.wait_for_function("!__scrimmage.live.state.enabled")
    page.wait_for_timeout(300)
    assert page.evaluate("__scrimmage.live.state.recording")   # Record wasn't pressed again

    page.click("#drive-hud")
    page.keyboard.press("Enter")
    page.wait_for_function("__scrimmage.live.state.enabled")
    page.click("#drive-pause")
    page.wait_for_function("__scrimmage.live.state.paused")
    page.keyboard.press("Space")
    page.wait_for_function("!__scrimmage.live.state.enabled")
    page.wait_for_timeout(300)
    assert page.evaluate("__scrimmage.live.state.paused")      # Pause wasn't pressed again (no Resume)
    stop(page)


def test_start_asks_before_discarding_an_unsaved_recording(drive_page):
    page = drive_page()
    start(page)
    page.click("#drive-record")
    page.wait_for_function("__scrimmage.live.state.recording")
    page.wait_for_timeout(300)
    page.click("#drive-record")
    page.wait_for_function("!__scrimmage.live.state.recording && __scrimmage.live.state.can_save")
    stop(page)

    page.click("#drive-start")    # the recording wasn't saved: Start asks first
    page.wait_for_selector("#discard-dialog[open]")
    assert "Start a new session and discard the recording?" in page.text_content("#discard-dialog")
    page.click("#discard-cancel")
    page.wait_for_function("!document.querySelector('#discard-dialog').open")
    page.wait_for_timeout(300)
    assert page.evaluate("__scrimmage.live.state.state") == "stopped" and page.evaluate("__scrimmage.live.state.can_save")
    assert page.is_hidden("#drive-banner") and not page.is_disabled("#drive-start")

    page.click("#drive-start")
    page.wait_for_selector("#discard-dialog[open]")
    page.click("#discard-go")
    page.wait_for_function("__scrimmage.live.state.state === 'running' && __scrimmage.live.state.readouts", timeout=60000)
    assert not page.evaluate("document.querySelector('#discard-dialog').open")
    assert not page.evaluate("__scrimmage.live.state.can_save")
    stop(page)


# Records the commands the page sends on its sockets (not the 50 Hz inputs).
SENT_CMDS = """
(() => {
  const send = WebSocket.prototype.send;
  window.__sentCmds = [];
  WebSocket.prototype.send = function (d) {
    if (typeof d === 'string' && d.includes('"cmd"')) window.__sentCmds.push(JSON.parse(d).cmd);
    return send.call(this, d);
  };
})();
"""


def test_the_mini_field_takes_drags_only_while_a_session_is_live(drive_page):
    page = drive_page(init_script=SENT_CMDS)
    page.wait_for_function("__scrimmage.miniField")
    assert page.get_attribute("#drive-field", "aria-disabled") == "true"
    assert "off" in page.get_attribute("#drive-pokes", "class")
    drag_on_field(page, (5, 3), (5, 4))   # before Start: ignored
    page.wait_for_timeout(300)
    assert "set_pose" not in page.evaluate("__sentCmds")

    start(page)
    page.wait_for_function("__scrimmage.live.frames > 0")
    page.wait_for_timeout(300)
    p = pose(page)
    assert (p["x"], p["y"]) == (pytest.approx(START[0], abs=0.1), pytest.approx(START[1], abs=0.1))
    assert "set_pose" not in page.evaluate("__sentCmds")
    assert page.get_attribute("#drive-field", "aria-disabled") == "false"
    assert "off" not in page.get_attribute("#drive-pokes", "class")
    drag_on_field(page, (5, 3), (5, 4))   # live: the robot goes there
    wait_pose_near(page, 5, 3)

    stop(page)
    page.wait_for_function("document.querySelector('#drive-field').getAttribute('aria-disabled') === 'true'")
    n = page.evaluate("__sentCmds.length")
    drag_on_field(page, (7, 3), (7, 4))   # stopped: ignored again
    page.wait_for_timeout(300)
    assert page.evaluate("__sentCmds.length") == n
