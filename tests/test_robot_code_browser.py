"""Browser tests of the Robot tab's robot code panel (headless Chromium): pointing at the robot code, the readiness
checklist, where the robot starts (the start picker and the picture), the panel's Start and check devices and Drive
buttons, and the start page's team-folder questions.

The fixtures (Chromium lookup, the Server helper) live in tests/test_assembler_browser.py.
"""
import json
import os
import shutil
import tomllib
from pathlib import Path

import pytest

from scrimmage.assembler import templates
from test_assembler_browser import Server, browser  # noqa: F401  (fixtures)

pytestmark = pytest.mark.browser

TESTS = Path(__file__).resolve().parent


@pytest.fixture
def code_page(browser, tmp_path, monkeypatch):
    """code_page(cfg) -> page on the Robot tab with the robot code panel shown (server in tmp_path)."""
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    monkeypatch.setenv("SCRIMMAGE_NT_CONNECT_TIMEOUT", "0.5")   # the fake robot serves no NetworkTables
    monkeypatch.delenv("SCRIMMAGE_CAD", raising=False)
    monkeypatch.delenv("SCRIMMAGE_CODE", raising=False)
    servers, contexts, logs = [], [], []

    def make(cfg: Path, panel: bool = True, viewport=(1400, 900)):
        srv = Server(cfg, cwd=tmp_path)
        servers.append(srv)
        ctx = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        contexts.append(ctx)
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        # a refused folder answers 400 (shown on the page): not a browser error
        page.on("console", lambda m: logs.append(f"console.{m.type}: {m.text}")
                if m.type == "error" and "status of 4" not in m.text else None)
        page.goto(srv.url)
        page.wait_for_function("window.__scrimmage && __scrimmage.mode")
        if panel:
            page.wait_for_function("window.__scrimmage && __scrimmage.state && __scrimmage.code")
            page.click("#center-code")
            page.wait_for_function("window.__scrimmage && __scrimmage.code && __scrimmage.code.state")
        return page

    yield make
    for c in contexts:
        c.close()
    for s in servers:
        s.stop()
    assert not logs, f"browser errors: {logs}"


def _robot(folder: Path, extra: str = "") -> Path:
    p = folder / "bot.toml"
    p.write_text(templates.new_config("swerve") + extra)
    return p


def _fake_program(folder: Path) -> Path:
    cmd = json.dumps(["{python}", "fake_robot_main.py", "--lockstep", "--phoenix"])
    return _robot(folder, f"\n[robot_program]\ncommand = {cmd}\ncwd = {json.dumps(str(TESTS))}\n")


def _raw(p: Path) -> dict:
    return tomllib.loads(p.read_text())


def _place(page, x0, y0, x1, y1):
    """Press on the start picker's field at (x0, y0) and drag to (x1, y1) (field meters)."""
    p = page.evaluate(f"__scrimmage.startPicker.toScreen({x0}, {y0})")
    q = page.evaluate(f"__scrimmage.startPicker.toScreen({x1}, {y1})")
    page.mouse.move(p["x"], p["y"])
    page.mouse.down()
    page.mouse.move(q["x"], q["y"], steps=6)
    page.mouse.up()


def test_pick_code_folder_shows_summary_and_checks(code_page, tmp_path):
    proj = tmp_path / "5098-2026"
    (proj / "vendordeps").mkdir(parents=True)
    (proj / "build.gradle").write_text("plugins { id 'java' }\n")
    (proj / "vendordeps" / "Phoenix6.json").write_text(json.dumps({"name": "CTRE-Phoenix (v6)"}))
    (tmp_path / "notes").mkdir()
    cfg = _robot(tmp_path)
    page = code_page(cfg)
    assert "No robot code picked yet" in page.text_content("#code-folder")
    # a folder that isn't a robot project: said in plain words, nothing written
    page.click("#code-browse")
    page.click("[data-use='notes']")
    page.wait_for_selector("#code-problem:not([hidden])")
    assert "robot project" in page.text_content("#code-problem")
    assert "robot_program" not in _raw(cfg)
    # the robot project: the summary, then the checklist with a fix button for the missing build
    page.click("[data-use='5098-2026']")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.info !== null")
    assert "Java (GradleRIO)" in page.text_content("#code-summary")
    assert "Phoenix 6" in page.text_content("#code-summary")
    assert page.is_hidden("#code-problem")
    rp = _raw(cfg)["robot_program"]
    assert rp["cwd"] == "5098-2026"
    page.wait_for_selector("#code-checks li[data-check='gradle_built']")
    row = page.locator("#code-checks li[data-check='gradle_built']")
    assert "⚠️" in row.locator(".check-icon").text_content()
    assert page.locator("#code-checks li[data-check='realtime'] .check-icon").text_content().strip() == "ℹ️"
    for c in page.evaluate("__scrimmage.code.state.checks"):
        icon = page.locator(f"#code-checks li[data-check='{c['id']}'] .check-icon").text_content().strip()
        assert icon == {True: "✅", False: "⚠️", None: "ℹ️"}[c["ok"]]
    # the fix runs (there is no gradlew here, so it fails at once): its output and the failure in plain words
    row.locator("button[data-fix='gradle_build']").click()
    page.wait_for_selector("#code-checks li[data-check='gradle_built'] .check-detail")
    assert "didn't build" in row.text_content()
    assert "gradlew" in row.locator(".check-detail").text_content()
    assert page.is_enabled("#code-checks li[data-check='gradle_built'] button[data-fix='gradle_build']")


def test_scenario_run_of_unbuilt_java_code_goes_to_build_now(code_page, tmp_path):
    proj = tmp_path / "5098-2026"
    proj.mkdir()
    (proj / "build.gradle").write_text("plugins { id 'java' }\n")
    cfg = _robot(tmp_path, f"\n[robot_program]\ncommand = [\"./gradlew\"]\ncwd = {json.dumps(str(proj))}\n")
    scn = tmp_path / "drive.toml"
    scn.write_text(f'config = "{cfg.name}"\n\n[[step]]\nname = "sit"\nmode = "disabled"\nduration = 0.5\n')
    page = code_page(scn, panel=False)
    page.wait_for_function("window.__scrimmage && __scrimmage.tab === 'scenario' && __scrimmage.scenario && __scrimmage.scenario.runs && __scrimmage.code")
    # Run refuses before asking where the robot starts, and points at Build now in the robot code panel
    page.click("#run-btn")
    page.wait_for_selector("#run-to-code")
    assert "hasn't been built on this computer yet" in page.text_content("#run-errors")
    assert page.is_hidden("#start-dialog") and page.evaluate("__scrimmage.scenario.runs.length") == 0
    page.click("#run-to-code")
    page.wait_for_function("window.__scrimmage && __scrimmage.tab === 'robot'")
    page.wait_for_selector("#code-checks li[data-check='gradle_built']")


def test_unbuilt_java_code_says_build_first(code_page, tmp_path):
    # a Java project that has never built here: the starts say so in plain words, with Build now right there
    proj = tmp_path / "5098-2026"
    proj.mkdir()
    (proj / "build.gradle").write_text("plugins { id 'java' }\n")
    cfg = _robot(tmp_path, f"\n[robot]\nstart_pose = [2.0, 2.0, 0.0]\n[robot_program]\ncommand = [\"./gradlew\"]\n"
                           f"cwd = {json.dumps(str(proj))}\n")
    cfg.write_text(cfg.read_text().replace("[robot]\n", "", 1))   # the template's own (empty) [robot] goes
    page = code_page(cfg)
    page.wait_for_selector("#code-checks li[data-check='gradle_built']")
    page.click("#code-check-devices")
    page.wait_for_selector("#code-run-status:not([hidden])")
    text = page.text_content("#code-run-status")
    assert "hasn't been built on this computer yet" in text and "null" not in text
    assert page.text_content("#code-run-build").strip() == "Build now (a few minutes)"
    assert page.is_hidden("#start-dialog") and page.evaluate("__scrimmage.code.devices.live") is False
    # Drive says the same, on the Robot tab
    page.click("#code-drive")
    page.wait_for_selector("#code-run-build")
    assert page.evaluate("__scrimmage.tab") == "robot"
    # Build now from there: no gradlew, so it fails; the start then says the last build failed, after a reload too
    page.click("#code-run-build")
    page.wait_for_selector("#code-checks li[data-check='gradle_built'] .check-detail")
    page.reload()
    page.wait_for_function("window.__scrimmage && __scrimmage.state && __scrimmage.code")
    page.click("#center-code")
    page.wait_for_selector("#code-checks li[data-check='gradle_built']")
    assert "didn't build" in page.text_content("#code-checks li[data-check='gradle_built']")
    page.click("#code-check-devices")
    page.wait_for_selector("#code-run-build")
    assert "didn't build the last time" in page.text_content("#code-run-status")
    # the Drive tab's own Start: refused by the server (start/not_built), its banner goes to the panel
    page.click("#tab-btn-drive")
    page.click("#drive-start")
    page.wait_for_selector("#drive-banner:not([hidden])")
    assert "didn't build the last time" in page.text_content("#drive-banner-text")
    assert page.text_content("#drive-banner-robot").strip() == "Go to Robot code"
    page.click("#drive-banner-robot")
    page.wait_for_function("window.__scrimmage && __scrimmage.tab === 'robot'")
    assert page.is_visible("#code-checks")


def test_all_devices_match_and_stop(code_page, tmp_path):
    # the normal case: every device the code made matches (the fake robot makes Talon FX 1; the rest is PWM, not
    # checked yet): nothing says "null", and Stop the robot is there while it runs
    cmd = json.dumps(["{python}", "fake_robot_main.py", "--lockstep", "--phoenix"])
    text = templates.new_config("differential").replace("[robot]\n", PICKED_START, 1)
    cfg = tmp_path / "bot.toml"
    cfg.write_text(text.replace('left = "PWM:0"', 'left = "TalonFX:1"')
                   + f"\n[robot_program]\ncommand = {cmd}\ncwd = {json.dumps(str(TESTS))}\n")
    page = code_page(cfg)
    assert page.is_hidden("#code-stop")
    page.click("#code-check-devices")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.devices && __scrimmage.code.devices.settled", timeout=60000)
    page.wait_for_selector("#code-devices tr[data-status='ok']")
    statuses = {r["status"] for r in page.evaluate("__scrimmage.code.devices.rows")}
    assert "missing" not in statuses and "other_type" not in statuses and "ok" in statuses, statuses
    assert "null" not in page.text_content("#code-devices")
    assert "null" not in page.text_content("#code-devices-state")
    assert "doesn't match" not in page.text_content("#code-devices-state")
    # pressing Start again while it runs: plain words, and the Stop button
    assert page.is_visible("#code-stop")
    page.click("#code-check-devices")
    page.wait_for_function("document.querySelector('#code-run-status').textContent.includes('already running')")
    text = page.text_content("#code-run-status")
    assert "drive session" not in text and "Stop the robot" in text
    page.click("#code-stop")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.devices && __scrimmage.code.devices.live === false")
    page.wait_for_selector("#code-stop", state="hidden")
    assert page.is_hidden("#code-run-status")
    assert page.locator("#code-devices tr").count() > 1      # the rows stay after it stops


def test_drive_asks_for_start_first(code_page, tmp_path):
    cfg = _fake_program(tmp_path)
    page = code_page(cfg)
    assert page.text_content("#start-sentence").strip() == "Not picked yet"
    # Drive opens the picker first; Cancel starts nothing and writes nothing
    page.click("#code-drive")
    page.wait_for_selector("#start-dialog[open]")
    page.click("#sp-cancel")
    page.wait_for_selector("#start-dialog", state="hidden")
    assert page.evaluate("__scrimmage.tab") == "robot"
    assert "start_pose" not in _raw(cfg).get("robot", {})
    # Start and check devices asks too; picking a spot then starts the robot (disabled)
    page.click("#code-check-devices")
    page.wait_for_selector("#start-dialog[open]")
    page.check("#sp-field-plain")
    _place(page, 3.0, 2.0, 4.0, 2.0)
    page.click("#sp-ok")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.start.picked")
    assert _raw(cfg)["robot"]["start_pose"] == pytest.approx([3.0, 2.0, 0.0], abs=0.02)
    page.wait_for_selector("#code-run-status:not([hidden])")
    assert "Started" in page.text_content("#code-run-status")
    # Drive now goes straight to the Drive tab, where the robot is already running (disabled)
    page.click("#code-drive")
    page.wait_for_function("window.__scrimmage && __scrimmage.tab === 'drive'")
    page.wait_for_function("window.__scrimmage && __scrimmage.live && __scrimmage.live.state && __scrimmage.live.state.state === 'running'")
    assert page.is_hidden("#start-dialog")
    page.click("#drive-stop")
    page.wait_for_function("window.__scrimmage && __scrimmage.live.state && !['running', 'starting', 'paused'].includes(__scrimmage.live.state.state)")


def test_pick_start_shows_sentence_and_triangle(code_page, tmp_path):
    from browser_harness import page_units
    cfg = _robot(tmp_path)
    page = code_page(cfg)
    page_units(page, "metric")      # the numbers below are meters
    assert page.text_content("#start-sentence").strip() == "Not picked yet"
    assert page.is_hidden("#start-canvas")
    page.click("#start-pick")
    page.wait_for_selector("#start-dialog[open]")
    # 2026: the named starts are quick choices and the floor-balls switch has its reason
    page.check("#sp-field-2026")
    page.wait_for_selector("#start-dialog [data-named='blue_center']")
    assert _raw(cfg)["range"]["file"] == "2026"
    assert page.is_visible("#sp-floor-balls")
    assert "slower" in page.text_content("#sp-floor-why")
    page.check("#sp-floor-balls")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.start.floor_balls === false")
    assert _raw(cfg)["range"]["floor_balls"] is False
    # the plain field: no named starts, no switch
    page.check("#sp-field-plain")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.start.range === null")
    assert "range" not in _raw(cfg)
    page.wait_for_selector("#start-dialog [data-named]", state="detached")
    assert page.is_hidden("#sp-floor-balls")
    # click on the blue side and drag toward the red wall; Exact position shows the numbers
    _place(page, 2.5, 3.0, 4.0, 3.0)
    page.click("#sp-exact summary")
    assert float(page.input_value("#sp-x")) == pytest.approx(2.5, abs=0.03)
    assert float(page.input_value("#sp-heading")) == pytest.approx(0, abs=1)
    page.fill("#sp-y", "3.5")
    page.click("#sp-ok")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.start.picked")
    pose = _raw(cfg)["robot"]["start_pose"]
    assert pose == pytest.approx([2.5, 3.5, 0.0], abs=0.03)
    assert page.text_content("#start-sentence").strip() == "Starts on the blue side, facing the red wall"
    assert page.is_visible("#start-canvas")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.picture")
    pic = page.evaluate("__scrimmage.code.picture")
    assert pic["pose"] == pytest.approx(pose, abs=1e-6)
    assert pic["labels"] == ["practice box"]     # the plain field's box in the middle, named so it isn't the robot
    # the robot at its real size (the swerve template's frame is about 0.7 m across) and the triangle facing +x
    xs = [p[0] for p in pic["footprint"]]
    assert 0.5 < max(xs) - min(xs) < 1.0
    tip = max(pic["triangle"], key=lambda p: p[0])
    assert tip[0] > pose[0] + 0.1 and abs(tip[1] - pose[1]) < 0.05
    page.click("#start-exact summary")
    assert "2.5" in page.text_content("#start-exact") and "3.5" in page.text_content("#start-exact")
    # turned to face the blue wall: the sentence follows
    page.click("#start-pick")
    page.wait_for_selector("#start-dialog[open]")
    page.click("#sp-exact summary")
    page.fill("#sp-heading", "180")
    page.click("#sp-ok")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.start.pose && __scrimmage.code.state.start.pose[2] === 180")
    assert page.text_content("#start-sentence").strip() == "Starts on the blue side, facing the blue wall"
    page.wait_for_function("window.__scrimmage && __scrimmage.code.picture && __scrimmage.code.picture.pose[2] === 180")
    tip = min(page.evaluate("__scrimmage.code.picture.triangle"), key=lambda p: p[0])
    assert tip[0] < 2.4


def test_cancel_undoes_the_field_changes(code_page, tmp_path):
    cfg = _robot(tmp_path, '\n[range]\nfile = "2026"\nstart = "blue_center"\n')
    before = cfg.read_text()
    page = code_page(cfg)
    assert page.text_content("#start-sentence").strip() == "Starts on the blue side, facing the red wall"
    page.click("#start-pick")
    page.wait_for_selector("#start-dialog[open]")
    # trying things out writes them (the field drawn is the robot's world) ...
    page.check("#sp-floor-balls")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.start.floor_balls === false")
    page.check("#sp-field-plain")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.start.range === null")
    assert "range" not in _raw(cfg)
    page.check("#sp-field-2026")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.start.range === '2026'")
    assert _raw(cfg)["range"] == {"file": "2026"}      # the named start went with the old field; none came back
    _place(page, 3.0, 3.0, 4.0, 3.0)
    # ... and Cancel takes every one of them back: the file is as it was before the picker opened
    page.click("#sp-cancel")
    page.wait_for_function("!__scrimmage.code.picking && __scrimmage.code.state.start.named === 'blue_center'")
    assert cfg.read_text() == before
    assert page.text_content("#start-sentence").strip() == "Starts on the blue side, facing the red wall"
    # Escape is a cancel too
    page.click("#start-pick")
    page.wait_for_selector("#start-dialog[open]")
    page.check("#sp-field-plain")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.start.range === null")
    page.keyboard.press("Escape")
    page.wait_for_function("!__scrimmage.code.picking && __scrimmage.code.state.start.named === 'blue_center'")
    assert cfg.read_text() == before


OTHER_CLIENT_EDIT = """async (body) => {
  const t = new URLSearchParams(location.hash.slice(1)).get('token');
  const hdr = {'X-Scrimmage-Token': t, 'Content-Type': 'application/json'};
  const st = await (await fetch('/api/robot/state', {headers: hdr})).json();
  const r = await fetch('/api/robot/edit', {method: 'POST', headers: hdr, body: JSON.stringify({version: st.version, ...body})});
  return await r.json();
}"""


def test_cancel_never_undoes_someone_elses_edit(code_page, tmp_path):
    cfg = _robot(tmp_path)
    page = code_page(cfg)
    page.click("#start-pick")
    page.wait_for_selector("#start-dialog[open]")
    page.check("#sp-field-2026")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.start.range === '2026'")
    # another client (a second tab, the agent) edits the robot while the picker is open
    r = page.evaluate(OTHER_CLIENT_EDIT, {"op": "battery", "nominal_voltage": 12.5})
    assert r["ok"] is True, r
    assert _raw(cfg)["battery"]["nominal_voltage"] == 12.5
    # Cancel undoes nothing: that edit stays, the picker's field stays, and the page says why
    page.click("#sp-cancel")
    page.wait_for_function("!__scrimmage.code.picking")
    page.wait_for_selector("#notices .notice-warn")
    assert "Someone else changed the robot" in page.text_content("#notices")
    raw = _raw(cfg)
    assert raw["battery"]["nominal_voltage"] == 12.5
    assert raw["range"] == {"file": "2026"}


def test_scenario_run_asks_for_start_first(code_page, tmp_path):
    cfg = _fake_program(tmp_path)
    scn = tmp_path / "drive.toml"
    scn.write_text('config = "bot.toml"\n\n[[step]]\nname = "sit"\nmode = "disabled"\nduration = 0.5\n')
    page = code_page(scn, panel=False)
    page.wait_for_function("window.__scrimmage && __scrimmage.tab === 'scenario' && __scrimmage.scenario && __scrimmage.scenario.runs && __scrimmage.code")
    # Run asks where the robot (the scenario's robot) starts, then runs from there
    page.click("#run-btn")
    page.wait_for_selector("#start-dialog[open]")
    page.check("#sp-field-plain")
    _place(page, 2.0, 2.0, 3.0, 2.0)
    page.click("#sp-ok")
    page.wait_for_function("window.__scrimmage && __scrimmage.scenario.runs.length === 1")
    assert _raw(cfg)["robot"]["start_pose"] == pytest.approx([2.0, 2.0, 0.0], abs=0.02)
    page.wait_for_function("window.__scrimmage && __scrimmage.scenario.runs[0].state !== 'running'", timeout=90000)
    assert page.evaluate("__scrimmage.scenario.runs[0].state") == "passed", page.text_content("#run-errors")
    assert page.text_content("#run-errors").strip() == ""


def test_start_page_asks_for_unset_folder(code_page, tmp_path):
    (tmp_path / "team-code").mkdir()
    cfg = _robot(tmp_path, '\n[robot_program]\ncommand = ["./gradlew"]\ncwd = "{code}/5098-2026"\n')
    page = code_page(cfg, panel=False)
    # the robot names the code folder, which this computer hasn't set: the start page asks, in plain words
    page.wait_for_selector("#start-folder-ask")
    assert "Where is your team's code on this computer?" in page.text_content("#start-folder-ask")
    assert page.is_visible("#start-folders")
    assert "Not set" in page.text_content("#start-folders [data-team='cad']")
    page.click("#start-folder-ask-browse")
    page.click("#start-folder-ask [data-folder='team-code']")
    # remembered for this computer; back on the robot, which can be edited again
    page.wait_for_selector("#start-page", state="hidden")
    page.wait_for_function("window.__scrimmage && __scrimmage.state && __scrimmage.state.read_only === false")
    saved = json.loads((tmp_path / "folders.json").read_text())
    assert Path(saved["code"]) == tmp_path / "team-code"
    # the start page's team folders section shows it, and Undo/Redo keys do nothing while it is shown
    page.click("#start-link")
    page.wait_for_selector("#start-folders [data-team='code']")
    assert "team-code" in page.text_content("#start-folders [data-team='code']")
    assert page.is_hidden("#start-folder-ask")


# -- the device check, motor settings, battery, contrast (Task 7) ---------------------------------------------------
PICKED_START = "[robot]\nstart_pose = [2.0, 2.0, 0.0]\n"


def _mismatched(folder: Path) -> Path:
    """The fake robot (it makes Talon FX 1) with fl.drive on Talon FX 21: nothing has that ID, and Talon FX 1 is left
    over."""
    cfg = _fake_program(folder)
    cfg.write_text(cfg.read_text().replace("[robot]\n", PICKED_START, 1)
                   .replace('drive = "TalonFX:1", steer = "TalonFX:2"', 'drive = "TalonFX:21", steer = "TalonFX:2"'))
    return cfg


def _check_settled(page):
    page.click("#code-check-devices")
    page.wait_for_selector("#code-run-status:not([hidden])")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.devices && __scrimmage.code.devices.settled", timeout=60000)
    page.wait_for_selector("#code-devices tr[data-slot='fl.drive'][data-status='missing']")


def _stop(page):
    page.evaluate("""async () => {
      const t = new URLSearchParams(location.hash.slice(1)).get('token');
      await fetch('/api/live/stop', {method: 'POST', headers: {'X-Scrimmage-Token': t, 'Content-Type': 'application/json'}, body: '{}'});
    }""")


def test_device_table_shows_mismatch_and_applies(code_page, tmp_path):
    cfg = _mismatched(tmp_path)
    page = code_page(cfg)
    assert page.is_hidden("#code-devices")
    _check_settled(page)
    assert "null" not in page.text_content("#code-run-status")   # an empty slot must not print "null"
    heads = [t.strip() for t in page.locator("#code-devices thead th").all_text_contents()]
    assert heads == ["Model part", "Model expects", "Your code made", "Status"]
    # the wrong ID: ❌ and a bold "nothing with this ID", with the same-type device the code made as a choice
    row = page.locator("#code-devices tr[data-slot='fl.drive']")
    assert row.locator(".dev-icon").text_content().strip() == "❌"
    assert row.locator("strong.dev-word").text_content().strip() == "Nothing with this ID"
    assert "TalonFX 21" in row.text_content()
    use = row.locator("button[data-use='TalonFX 1']")
    assert use.text_content().strip() == "Use TalonFX 1"
    # the device the code made that the model lacks: ➕ and "Add as…", which opens the part inspector's IDs
    extra = page.locator("#code-devices tr[data-status='extra'][data-made='TalonFX 1']")
    assert extra.locator(".dev-icon").text_content().strip() == "➕"
    assert extra.locator("strong.dev-word").text_content().strip() == "Not in the model"
    extra.locator("button[data-add]").click()
    page.select_option("#code-add-part", "fr")
    page.wait_for_function("window.__scrimmage && __scrimmage.selected === 'fr' && document.activeElement && document.activeElement.id === 'insp-id-drive'")
    assert "TalonFX:1" in page.text_content("#notices")
    assert 'drive = "TalonFX:3"' in cfg.read_text()            # nothing written: the student types it in
    # rows that match show ✅; the Apply button needs a choice first
    assert page.locator("#code-devices tr[data-slot='fl.steer'] .dev-icon").text_content().strip() in ("❌", "✅")
    assert page.is_disabled("#code-apply")
    use.click()
    assert use.get_attribute("aria-pressed") == "true"
    assert page.is_enabled("#code-apply")
    assert page.text_content("#code-apply").strip() == "Apply the matches and restart"
    page.click("#code-apply")
    page.wait_for_selector("#code-apply-status:not([hidden])")
    # the restart happens after the answer: "restarting" first, then what came of it
    assert "restart" in page.text_content("#code-apply-status")
    page.wait_for_function("document.querySelector('#code-apply-status').textContent.includes('restarted with the new IDs')",
                           timeout=60000)
    assert _raw(cfg)["part"][1]["ids"]["drive"] == "TalonFX:1"
    page.wait_for_selector("#code-devices tr[data-slot='fl.drive'][data-status='ok']", timeout=60000)
    assert page.locator("#code-devices tr[data-slot='fl.drive'] .dev-icon").text_content().strip() == "✅"
    assert page.locator("#code-devices tr[data-slot='fl.drive'] strong.dev-word").text_content().strip() == "Matches"
    # one Undo takes the whole apply back
    page.click("#undo")
    page.wait_for_function("window.__scrimmage && __scrimmage.state.parts[1].ids.drive === 'TalonFX:21'")
    _stop(page)


def test_drive_tab_shows_device_mismatch_line(code_page, tmp_path):
    cfg = _mismatched(tmp_path)
    page = code_page(cfg)
    _check_settled(page)
    rows = page.evaluate("__scrimmage.code.devices.rows")
    bad = sum(r["status"] in ("missing", "other_type") for r in rows)
    assert bad > 1
    page.click("#tab-btn-drive")
    page.wait_for_selector("#drive-devices:not([hidden])")
    assert page.text_content("#drive-devices").strip() == f"{bad} devices don't match the model: see Robot code"
    page.click("#drive-devices button")
    page.wait_for_function("window.__scrimmage && __scrimmage.tab === 'robot'")
    assert page.is_visible("#code-panel") and page.is_visible("#code-devices")
    _stop(page)


def test_brake_control_writes_neutral(code_page, tmp_path):
    cfg = _robot(tmp_path)
    page = code_page(cfg, panel=False)
    page.evaluate("__scrimmage.select('fl')")
    page.wait_for_selector("#insp-neutral-drive")
    box = page.locator('[data-field="fl/ids:drive"] .motor-settings')
    assert "can't read" in box.text_content()
    # a Talon FX coasts unless the code says brake
    assert page.locator("#insp-neutral-drive option[value='']").text_content().strip() == "Default (coast)"
    page.select_option("#insp-neutral-drive", "brake")
    page.wait_for_function("JSON.stringify(__scrimmage.state.parts[1].ids.drive).includes('brake')")
    assert 'drive = { ctre = "TalonFX:1", neutral = "brake" }' in cfg.read_text()
    page.check("#insp-invert-drive")
    page.wait_for_function("JSON.stringify(__scrimmage.state.parts[1].ids.drive).includes('invert')")
    assert _raw(cfg)["part"][1]["ids"]["drive"] == {"ctre": "TalonFX:1", "neutral": "brake", "invert": True}
    assert page.is_checked("#insp-invert-drive")
    assert page.input_value("#insp-neutral-drive") == "brake"
    # back to the defaults: the short ID again
    page.select_option("#insp-neutral-drive", "")
    page.uncheck("#insp-invert-drive")
    page.wait_for_function("window.__scrimmage && __scrimmage.state.parts[1].ids.drive === 'TalonFX:1'")
    assert 'drive = "TalonFX:1", steer = "TalonFX:2"' in cfg.read_text()
    # an encoder has no such controls
    assert page.locator("#insp-neutral-encoder").count() == 0


def test_battery_fields_write_and_keep_typing(code_page, tmp_path):
    cfg = _robot(tmp_path)
    page = code_page(cfg)
    page.click("#code-more summary")
    assert page.input_value("#code-battery-voltage") == "12.5"
    assert page.input_value("#code-battery-resistance") == "0.02"
    assert "12" in page.get_attribute("#code-battery-voltage", "placeholder")     # the default, when left empty
    assert "battery" in page.text_content("#code-more").lower()
    page.fill("#code-battery-voltage", "12.8")
    page.keyboard.press("Enter")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.battery.nominal_voltage === 12.8")
    assert _raw(cfg)["battery"]["nominal_voltage"] == 12.8
    # half-typed text survives the panel redrawing (another edit reloads it)
    page.fill("#code-battery-resistance", "0.0")
    page.evaluate(OTHER_CLIENT_EDIT, {"op": "battery", "nominal_voltage": 12.6})
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.battery.nominal_voltage === 12.6")
    assert page.input_value("#code-battery-resistance") == "0.0"
    assert page.input_value("#code-battery-voltage") == "12.6"
    # a bad value is shown beside the field, nothing written
    page.fill("#code-battery-resistance", "-1")
    page.keyboard.press("Enter")
    page.wait_for_selector("#code-more .error")
    assert _raw(cfg)["battery"]["resistance"] == 0.02


def test_undo_with_an_old_version_is_refused(code_page, tmp_path):
    cfg = _robot(tmp_path)
    page = code_page(cfg)
    v = page.evaluate("__scrimmage.state.version")
    r = page.evaluate(OTHER_CLIENT_EDIT, {"op": "battery", "nominal_voltage": 12.7})
    assert r["ok"] is True
    page.wait_for_function(f"__scrimmage.state.version > {v}")
    # an undo for the version the page saw before: the server refuses it, so the other client's edit stays
    r = page.evaluate(f"import('/app.js').then((m) => m.undo({v}))")
    assert r["ok"] is False
    assert _raw(cfg)["battery"]["nominal_voltage"] == 12.7


def test_panel_contrast_light_and_dark(code_page, tmp_path):
    from test_assembler_browser import CONTRAST_JS
    cfg = _mismatched(tmp_path)
    page = code_page(cfg, viewport=(1920, 1080))
    _check_settled(page)
    page.click("#code-more summary")
    for scheme in ("light", "dark"):
        page.emulate_media(color_scheme=scheme)
        page.wait_for_timeout(100)
        bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
        dark = page.evaluate(f"({CONTRAST_JS}).lum(getComputedStyle(document.body).backgroundColor) < 0.2")
        assert dark == (scheme == "dark"), (scheme, bg)
        for sel in ("#code-panel", "#code-devices", "#inspector"):
            bad = page.evaluate(f"({CONTRAST_JS}).check(document.querySelector({json.dumps(sel)}))")
            assert not bad, (scheme, sel, bad)
        page.click("#start-pick")
        page.wait_for_selector("#start-dialog[open]")
        bad = page.evaluate(f"({CONTRAST_JS}).check(document.querySelector('#start-dialog'))")
        assert not bad, (scheme, "picker", bad)
        page.click("#sp-cancel")
        page.wait_for_function("!__scrimmage.code.picking")
    _stop(page)


def test_use_choices_are_one_slot_per_device(code_page, tmp_path):
    cfg = _mismatched(tmp_path)
    page = code_page(cfg)
    _check_settled(page)
    drive = page.locator("#code-devices tr[data-slot='fl.drive'] button[data-use='TalonFX 1']")
    steer = page.locator("#code-devices tr[data-slot='fl.steer'] button[data-use='TalonFX 1']")
    drive.click()
    assert drive.get_attribute("aria-pressed") == "true"
    # the same device chosen for another slot moves the choice there
    steer.click()
    assert steer.get_attribute("aria-pressed") == "true"
    assert drive.get_attribute("aria-pressed") == "false"
    with page.expect_request(lambda r: r.url.endswith("/api/robot/edit") and "match_devices" in (r.post_data or "")) as req:
        page.click("#code-apply")
    assert json.loads(req.value.post_data)["matches"] == {"fl.steer": "TalonFX 1"}
    page.wait_for_selector("#code-apply-status:not([hidden])")
    ids = _raw(cfg)["part"][1]["ids"]
    assert ids["steer"] == "TalonFX:1" and ids["drive"] == "TalonFX:21"
    _stop(page)


@pytest.mark.skipif(not os.environ.get("SCRIMMAGE_ROBOT_CODE_SHOT"),
                    reason="set SCRIMMAGE_ROBOT_CODE_SHOT=<png> to write the docs screenshot (docs/img/robot-code.png)")
def test_docs_screenshot(code_page, tmp_path):
    # the robot code is a folder inside tmp_path, so the picture shows no home path; the start is picked and one
    # device (fl.drive on Talon FX 21) doesn't match what the fake robot made
    proj = tmp_path / "my-robot"
    shutil.copytree(TESTS, proj, ignore=shutil.ignore_patterns("__pycache__", "*.so"), dirs_exist_ok=True)
    (proj / "vendordeps").mkdir()
    (proj / "build.gradle").write_text("plugins { id 'java' }\n")
    (proj / "vendordeps" / "Phoenix6.json").write_text(json.dumps({"name": "CTRE-Phoenix (v6)"}))
    (proj / "build" / "libs").mkdir(parents=True)               # built before: the start isn't refused
    (proj / "build" / "libs" / "robot.jar").write_bytes(b"")
    cfg = _mismatched(tmp_path)
    cfg.write_text(cfg.read_text().replace(f'cwd = {json.dumps(str(TESTS))}', f'cwd = {json.dumps(str(proj))}'))
    page = code_page(cfg, viewport=(1500, 1500))
    _check_settled(page)
    page.wait_for_function("window.__scrimmage && __scrimmage.code.picture")
    page.click("#code-devices tr[data-slot='fl.drive'] button")   # choose "Use TalonFX 1"
    page.evaluate("document.querySelector('#code-panel').scrollTop = 0")   # parts 1 to 3, then the table
    page.wait_for_timeout(300)
    # the checklist names where the JDK was found (a path under this computer's home): shown as ~ in the picture
    page.evaluate("""(home) => { const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      for (let n = w.nextNode(); n; n = w.nextNode()) if (n.nodeValue.includes(home)) n.nodeValue = n.nodeValue.split(home).join('~'); }""",
                  str(Path.home()))
    shown = page.inner_text("body")
    for bad in (str(tmp_path), str(TESTS.parent), str(Path.home()), "/tmp/"):
        i = shown.find(bad)
        assert i < 0, shown[max(0, i - 120):i + 120]
    page.screenshot(path=os.environ["SCRIMMAGE_ROBOT_CODE_SHOT"])
    _stop(page)


def _live_real_ds(page) -> bool:
    """Whether the running session is a real-DS one (its config's default is known only once it runs)."""
    return page.evaluate("""async () => {
      const t = new URLSearchParams(location.hash.slice(1)).get('token');
      for (;;) {
        const s = (await (await fetch('/api/live/info', {headers: {'X-Scrimmage-Token': t}})).json()).status;
        if (s.state === 'running') return s.real_ds;
        await new Promise((r) => setTimeout(r, 100));
      }
    }""")


def test_check_devices_takes_the_drive_tabs_driver_station_choice(code_page, tmp_path):
    """Start and check devices starts the session the Drive tab would: the config's [driver_station] real until the
    student picks in the Drive tab, then the pick."""
    cmd = json.dumps(["{python}", "fake_robot_main.py", "--lockstep"])
    cfg = tmp_path / "bot.toml"
    cfg.write_text(templates.new_config("differential").replace("[robot]\n", PICKED_START, 1)
                   + f"\n[robot_program]\ncommand = {cmd}\ncwd = {json.dumps(str(TESTS))}\n"
                   + "\n[driver_station]\nreal = true\n")
    page = code_page(cfg)
    for pick, want in ((None, True), ("page", False), ("real", True)):
        if pick is not None:
            page.click("#tab-btn-drive")
            page.wait_for_selector("#drive-ds-source:enabled")
            page.select_option("#drive-ds-source", pick)
            page.click("#tab-btn-robot")
            page.click("#center-code")
        page.click("#code-check-devices")
        page.wait_for_selector("#code-stop:visible", timeout=60000)
        assert _live_real_ds(page) is want, pick
        page.wait_for_function("document.querySelector('#code-run-status').textContent.includes('Enable it in')")
        assert ("FRC Driver Station app" in page.text_content("#code-run-status")) is want, pick
        page.click("#code-stop")
        page.wait_for_selector("#code-stop", state="hidden", timeout=30000)


def test_the_start_picker_shows_and_takes_the_chosen_units(code_page, tmp_path):
    """Units (Settings): the start pose's x and y are field lengths, feet (the default) or meters; the file keeps
    meters, and a box not changed keeps its exact value."""
    from browser_harness import page_units
    cfg = _robot(tmp_path)
    page = code_page(cfg)
    page.click("#start-pick")
    page.wait_for_selector("#start-dialog[open]")
    page.check("#sp-field-plain")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.start.range === null")
    _place(page, 2.5, 3.0, 4.0, 3.0)
    page.click("#sp-exact summary")
    assert page.inner_text("#sp-exact .fp-field .unit") == "ft"
    assert float(page.input_value("#sp-x")) == pytest.approx(2.5 / 0.3048, abs=0.1)
    page.fill("#sp-y", "11.5 ft")
    page.click("#sp-ok")
    page.wait_for_function("window.__scrimmage && __scrimmage.code.state.start.picked")
    pose = _raw(cfg)["robot"]["start_pose"]
    assert pose[1] == pytest.approx(3.505) and pose[0] == pytest.approx(2.5, abs=0.03)
    page.click("#start-exact summary")
    assert "11.5 ft" in page.text_content("#start-exact")
    page_units(page, "metric")
    page.wait_for_function("document.querySelector('#start-exact').textContent.includes('3.505 m')")
    # the other system's unit is refused, saying why
    page.click("#start-pick")
    page.wait_for_selector("#start-dialog[open]")
    page.click("#sp-exact summary")
    assert page.input_value("#sp-y") == "3.505"
    page.fill("#sp-x", "8 ft")
    page.click("#sp-ok")
    page.wait_for_selector("#sp-errors:not([hidden])")
    assert "Units is set to Metric, so type m here, not ft" in page.text_content("#sp-errors")
    page.click("#sp-cancel")
    assert _raw(cfg)["robot"]["start_pose"] == pose
