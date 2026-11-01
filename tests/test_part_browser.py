"""Browser tests of the Part tab (`scrimmage edit climber.step`), headless Chromium through Playwright.

The fixtures (Chromium lookup, the Server helper) live in tests/test_assembler_browser.py. Opening the copied
tests/data/cad/climber.step makes parts/climber.import.toml next to it; its five bodies are base_plate (with six
bolts folded in as hardware), arm_A, arm_B, hook_assy and hook_assy_2.
"""
import shutil
import time
import tomllib
from pathlib import Path

import numpy as np

import pytest

from test_assembler_browser import Server, browser  # noqa: F401  (fixtures)

pytestmark = pytest.mark.browser

DATA = Path(__file__).resolve().parent / "data" / "cad"
ARM = "Climber/arm_A"
BOLT = "Climber/bolt"
READY = ("window.__scrimmagePart && __scrimmagePart.store.state && __scrimmagePart.scene"
         " && __scrimmagePart.scene.drawn.length > 0")


def _open(browser, tmp_path, monkeypatch, name):
    """(page, the import file, close) on `scrimmage edit tmp_path/<name>`, with its own recent list and cache."""
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    shutil.copy(DATA / name, tmp_path / name)
    srv = Server(tmp_path / name, cwd=tmp_path)
    ctx = browser.new_context(viewport={"width": 1500, "height": 950})
    logs = []
    page = ctx.new_page()
    page.set_default_timeout(30000)
    page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
    page.on("console", lambda m: logs.append(f"console.{m.type}: {m.text}")
            if m.type == "error" and "status of 4" not in m.text else None)

    def close():
        ctx.close()
        srv.stop()
        assert not logs, f"browser errors: {logs}"
    try:
        page.goto(srv.url)
        page.wait_for_function(READY)
    except Exception:
        ctx.close()
        srv.stop()
        raise
    return page, tmp_path / "parts" / (Path(name).stem + ".import.toml"), close


@pytest.fixture
def part_page(browser, tmp_path, monkeypatch):
    """(page, the import file) on `scrimmage edit tmp_path/climber.step`."""
    page, imp, close = _open(browser, tmp_path, monkeypatch, "climber.step")
    yield page, imp
    close()


@pytest.fixture
def yup_page(browser, tmp_path, monkeypatch):
    """(page, the import file) on climber_yup.step: the same climber drawn with +y up, as Inventor saves it."""
    page, imp, close = _open(browser, tmp_path, monkeypatch, "climber_yup.step")
    yield page, imp
    close()


def _piece(page, key):
    return page.evaluate(f"__scrimmagePart.store.state.pieces.find(p => p.key === {key!r})")


def _click_piece_in_3d(page, key, shift=False):
    pos = page.evaluate(f"__scrimmagePart.screenPos({key!r})")
    assert pos, f"{key} is not on screen"
    if shift:
        page.keyboard.down("Shift")
    page.mouse.click(pos["x"], pos["y"])
    if shift:
        page.keyboard.up("Shift")


def _changed(page, v0):
    page.wait_for_function(f"__scrimmagePart.store.state.version > {v0}")


def test_climber_shows_five_bodies(part_page):
    page, imp = part_page
    rows = page.locator("#part-tree [data-body]")
    assert rows.count() == 5
    assert [rows.nth(i).get_attribute("data-body") for i in range(5)] == \
        ["base_plate", "arm_A", "arm_B", "hook_assy", "hook_assy_2"]
    # the bolts are folded under base_plate as hardware
    assert page.locator(f'#part-tree [data-piece="{BOLT}"]').count() == 0 or \
        not page.locator(f'#part-tree [data-piece="{BOLT}"]').is_visible()
    assert "+6 hardware" in page.inner_text("#part-tree")
    assert page.inner_text("#part-build") in ("Parts up to date", "Updating parts…")
    assert imp.is_file()


def test_click_in_3d_selects_the_tree_row(part_page):
    page, _ = part_page
    _click_piece_in_3d(page, ARM)
    row = page.locator(f'#part-tree [data-piece="{ARM}"]')
    page.wait_for_function(f"document.querySelector('#part-tree [data-piece=\"{ARM}\"]').getAttribute('aria-selected') === 'true'")
    assert row.get_attribute("aria-selected") == "true"
    assert page.evaluate("[...__scrimmagePart.store.sel.keys]") == [ARM]
    # Shift-click adds the other arm to the selection
    _click_piece_in_3d(page, "Climber/arm_B", shift=True)
    page.wait_for_function("__scrimmagePart.store.sel.keys.size === 2")


def test_steel_raises_the_mass_and_is_saved(part_page):
    page, imp = part_page
    _click_piece_in_3d(page, ARM)
    page.wait_for_selector("#part-material")
    before = float(page.get_attribute("#part-sel-mass", "data-kg"))
    v0 = page.evaluate("__scrimmagePart.store.state.version")
    page.select_option("#part-material", "steel")
    _changed(page, v0)
    page.wait_for_function(f"__scrimmagePart.store.state.pieces.find(p => p.key === {ARM!r}).material === 'steel'")
    page.wait_for_function(f"+document.querySelector('#part-sel-mass').dataset.kg > {before}")
    after = float(page.get_attribute("#part-sel-mass", "data-kg"))
    assert after == pytest.approx(before * 7850 / 2700, rel=1e-3)
    assert 'material = "steel"' in imp.read_text()


def test_drag_a_bolt_onto_arm_a(part_page):
    page, _ = part_page
    page.click('#part-tree [data-hardware="base_plate"]')
    bolt = page.locator(f'#part-tree [data-piece="{BOLT}"]')
    bolt.wait_for()
    v0 = page.evaluate("__scrimmagePart.store.state.version")
    bolt.drag_to(page.locator('#part-tree [data-body="arm_A"]'))
    _changed(page, v0)
    page.wait_for_function(f"__scrimmagePart.store.state.pieces.find(p => p.key === {BOLT!r}).body === 'arm_A'")


def test_leave_out_hides_the_piece(part_page):
    page, imp = part_page
    hook = "Climber/hook_assy/hook"
    page.click(f'#part-tree [data-piece="{hook}"]')
    assert hook in page.evaluate("__scrimmagePart.scene.drawn")
    v0 = page.evaluate("__scrimmagePart.store.state.version")
    page.click("#part-leave-out")
    _changed(page, v0)
    assert _piece(page, hook)["left_out"] is True
    page.wait_for_function(f"!__scrimmagePart.scene.drawn.includes({hook!r})")
    assert page.evaluate(f"__scrimmagePart.screenPos({hook!r})") is None
    # "Show left out" draws it faintly again
    page.click("#part-show-left-out")
    page.wait_for_function(f"__scrimmagePart.scene.faint.includes({hook!r})")
    assert "left_out = true" in imp.read_text()


def test_checklist_asks_for_materials_until_they_are_set(part_page):
    page, _ = part_page
    items = page.locator("#part-checklist li", has_text="still guessed as aluminum")
    assert items.count() == 5
    for _ in range(5):
        items.first.locator("button").click()
        page.wait_for_selector("#part-material")
        v0 = page.evaluate("__scrimmagePart.store.state.version")
        page.select_option("#part-material", "aluminum")
        _changed(page, v0)
    page.wait_for_function("!document.querySelector('#part-checklist').textContent.includes('still guessed as aluminum')")
    assert all(p["material"] == "aluminum" for p in page.evaluate("__scrimmagePart.store.state.pieces"))


@pytest.mark.parametrize("how", ["browse", "upload"])
def test_new_part_from_cad_on_the_start_page(browser, tmp_path, monkeypatch, how):
    """"New part from CAD" opens a STEP file picked with Browse (used where it is) or sent from the file chooser (copied
    into parts/climber.source/), and the page comes back on the Part tab."""
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    shutil.copy(DATA / "climber.step", tmp_path / "climber.step")
    srv = Server(None, cwd=tmp_path)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    try:
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.goto(srv.url)
        page.click('[data-key="new-part"]')
        page.wait_for_selector("#start-drop")
        if how == "browse":
            page.click("#start-cad-browse-btn")
            page.click('#start-cad-browse [data-entry="climber.step"]')
        else:
            page.set_input_files("#start-cad-file", str(tmp_path / "climber.step"))
        page.wait_for_function(READY)
        assert page.locator("#part-tree [data-body]").count() == 5
        assert (tmp_path / "parts" / "climber.import.toml").is_file()
        assert (tmp_path / "parts" / "climber.source" / "climber.step").is_file() == (how == "upload")
    finally:
        ctx.close()
        srv.stop()


# ---------------------------------------------------------------- Task 13: frame, joints, slots, mounts, collision

HOOK = "Climber/hook_assy/hook"


def _body(page, name):
    return page.evaluate(f"__scrimmagePart.store.state.bodies.find(b => b.name === {name!r})")


def _pick_body(page, name):
    page.click(f'#part-tree [data-body="{name}"]')
    page.wait_for_function(f"__scrimmagePart.store.sel.kind === 'body' && __scrimmagePart.store.sel.keys.has({name!r})")


def _edit(page, fn):
    """Run fn (a UI action that makes one edit) and wait until the page shows the state the edit returned."""
    with page.expect_response(lambda r: r.url.split("?")[0].endswith("/api/part/edit") and r.request.method == "POST") as resp:
        fn()
    body = resp.value.json()
    assert resp.value.status == 200, body
    page.wait_for_function(f"__scrimmagePart.store.state.version >= {body['version']}")


def _type(page, sel, text):
    """Type into a box as a student does (keys, then Enter): a redraw while typing keeps the text."""
    page.click(sel, click_count=3)
    page.keyboard.type(text)
    page.keyboard.press("Enter")


def _wait_file(path_fn, pred, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        p = path_fn()
        if p is not None and p.is_file() and pred(p.read_text()):
            return p.read_text()
        time.sleep(0.25)
    raise AssertionError("the part folder never got the expected change")


def test_y_is_up_puts_the_floor_under_the_base_plate(yup_page):
    page, imp = yup_page
    banner = page.locator("#part-frame-step")
    banner.wait_for()
    assert banner.is_visible()
    assert "robot" in banner.inner_text().lower()
    # the default guess (z up) has the robot on its side: the arms lie along y
    arm = _body(page, "arm_A")["com"]
    assert arm[2] < 0.2
    _edit(page, lambda: page.click("#part-frame-yup"))
    f = page.evaluate("__scrimmagePart.store.state.frame")
    assert f["up"] == "+y" and f["front"][1] != "y"
    # the floor is the base plate's underside (CAD y = 0), so the floor grid (robot z = 0) is under it
    assert f["floor"] == pytest.approx(0.0, abs=1e-6)
    page.wait_for_function("__scrimmagePart.store.state.bodies.find(b => b.name === 'arm_A').com[2] > 0.3")
    plate = _body(page, "base_plate")["com"]
    assert 0 < plate[2] < 0.03
    assert _body(page, "arm_A")["com"][2] == pytest.approx(0.41, abs=0.03)
    page.wait_for_function("__scrimmagePart.scene.lowest !== null && Math.abs(__scrimmagePart.scene.lowest) < 1e-4")
    assert banner.is_visible(), "the step stays until the student says it looks right"
    assert f["confirmed"] is False
    page.reload()
    page.wait_for_function(READY)
    page.wait_for_selector("#part-frame-step")      # still asking after a reload
    assert "Check which way is up" in page.inner_text("#part-checklist")
    _edit(page, lambda: page.click("#part-frame-ok"))
    assert page.evaluate("__scrimmagePart.store.state.frame.confirmed") is True
    page.wait_for_selector("#part-frame-step", state="hidden")
    assert "up = \"+y\"" in imp.read_text()
    assert "Check which way is up" not in page.inner_text("#part-checklist")


def test_frame_math_in_the_page_matches_frame_py(yup_page):
    from scrimmage.cad.frame import RobotFrame, to_robot
    page, _ = yup_page
    frames = [RobotFrame(), RobotFrame("+y", "+x", 0.1, (0.15, -0.1)), RobotFrame("+y", "-z", -0.2, (1, 2)),
              RobotFrame("-y", "+z", 0.3, None), RobotFrame("-x", "+y", 0.5, (0.2, 0.3)),
              RobotFrame("+z", "-y", 0.0, (0.0, 0.0), skip=True)]
    for fr in frames:
        R, t = to_robot(fr)
        got = page.evaluate("f => __scrimmagePart.toRobot(f)", {"up": fr.up, "front": fr.front, "floor": fr.floor,
                                                                 "center": fr.center, "skip": fr.skip})
        assert np.allclose(got["R"], R) and np.allclose(got["t"], t), (fr, got)


def test_hook_turns_on_arm_a_and_try_it_moves_it(part_page):
    page, imp = part_page
    _pick_body(page, "hook_assy")
    _edit(page, lambda: page.select_option("#part-joint-parent", "arm_A"))
    assert _body(page, "hook_assy")["parent"] == "arm_A"
    # Turns is only a draft until the axis is picked: nothing is saved, and Esc drops it
    page.select_option("#part-joint-kind", "hinge")
    page.wait_for_selector("#part-joint-draft")
    page.wait_for_selector("#part-pick-hint")
    assert _body(page, "hook_assy")["joint"] is None
    page.keyboard.press("Escape")
    page.wait_for_selector("#part-pick-hint", state="hidden")
    page.wait_for_selector("#part-joint-draft", state="hidden")
    assert page.input_value("#part-joint-kind") == ""
    assert "joint" not in imp.read_text().split("[bodies.hook_assy]")[1].split("[bodies.")[0]
    page.select_option("#part-joint-kind", "hinge")
    page.click("#part-pick-axis")
    page.wait_for_selector("#part-pick-hint")
    # click the hook's round side (its triangles whose normal is across the x axis)
    pos = page.evaluate(f"__scrimmagePart.screenPos({HOOK!r}, {{across: [1, 0, 0]}})")
    assert pos, "the hook's side is not on screen"
    _edit(page, lambda: page.mouse.click(pos["x"], pos["y"]))
    j = _body(page, "hook_assy")["joint"]
    assert j["kind"] == "hinge"
    assert "turns on arm_A" in page.inner_text('#part-tree [data-body="hook_assy"]')
    assert np.allclose(np.abs(j["axis"]), [1, 0, 0], atol=1e-3)
    assert np.allclose(j["anchor"][1:], [0.1, 0.818], atol=2e-3)
    page.wait_for_selector("#part-pick-hint", state="hidden")
    assert "hook_assy" in page.evaluate("__scrimmagePart.scene.arrows")
    # Try it: the slider turns the hook in the view only
    sent = []
    page.on("request", lambda r: sent.append(r.url) if r.method == "POST" else None)
    saved = imp.read_text()
    before = page.evaluate(f"__scrimmagePart.screenPoint({HOOK!r})")
    page.fill("#part-try-hook_assy", "90")
    page.wait_for_function(f"(() => {{ const p = __scrimmagePart.screenPoint({HOOK!r}); return Math.hypot(p.x - {before['x']}, p.y - {before['y']}) > 2; }})()")
    page.wait_for_timeout(400)
    assert not sent, f"Try it never edits the part: {sent}"
    assert imp.read_text() == saved


def test_neo_on_a_sliding_arm_writes_its_gearing(part_page):
    page, imp = part_page
    # the hooks ride on arm_A (welded to it), and arm_A slides on the base plate
    _pick_body(page, "hook_assy")
    _edit(page, lambda: page.select_option("#part-joint-parent", "arm_A"))
    _pick_body(page, "arm_A")
    page.select_option("#part-joint-kind", "slide")
    page.wait_for_selector("#part-pick-hint")
    _edit(page, lambda: page.click("#part-snap-z"))     # snapping saves the slide too
    page.wait_for_selector("#part-pick-hint", state="hidden")
    assert np.allclose(_body(page, "arm_A")["joint"]["axis"], [0, 0, 1])
    page.select_option("#part-motor", "NEO")
    _type(page, "#part-motor-ratio", "25")
    page.wait_for_function("document.querySelector('#part-motor-ratio').dataset.orig === '25'")
    page.click("#part-spool-in")
    _edit(page, lambda: _type(page, "#part-spool", "1"))
    m = _body(page, "arm_A")["motor"]
    assert m["motor"] == "NEO" and m["ratio"] == 25 and m["spool_diameter"] == pytest.approx(0.0254)
    text = _wait_file(lambda: next(iter(sorted(imp.parent.glob("*/part.toml"))), None), lambda t: "NEO" in t)
    slots = tomllib.loads(text)["motor"]
    assert slots[0]["gearing"] == pytest.approx(1968.5, rel=1e-3)
    # Try it on arm_A slides the hook too (it hangs on arm_A)
    before = page.evaluate(f"__scrimmagePart.screenPoint({HOOK!r})")
    page.focus("#part-try-arm_A")
    page.keyboard.press("End")      # the keyboard moves it too: to the end of its +-0.5 m
    page.wait_for_function(f"(() => {{ const p = __scrimmagePart.screenPoint({HOOK!r}); return Math.hypot(p.x - {before['x']}, p.y - {before['y']}) > 5; }})()")


def test_wheel_collision_writes_a_cylinder(part_page):
    page, imp = part_page
    _pick_body(page, "hook_assy_2")
    # a body named like a hook gets a suggestion, which is never applied by itself
    assert "looks like a hook" in page.inner_text("#part-inspector")
    assert _body(page, "hook_assy_2")["collision"] == "none"
    page.select_option("#part-joint-kind", "hinge")
    _edit(page, lambda: page.click("#part-snap-x"))
    _edit(page, lambda: page.select_option("#part-collision", "wheel"))
    assert _body(page, "hook_assy_2")["collision"] == "wheel"
    text = _wait_file(lambda: next(iter(sorted(imp.parent.glob("*/part.toml"))), None), lambda t: "cylinder" in t)
    body = next(b for b in tomllib.loads(text)["body"] if b["name"] == "hook_assy_2")
    assert any("cylinder" in g for g in body["geom"])


def test_add_a_mount_a_coupling_and_a_pin(part_page):
    page, imp = part_page
    # a mount on the base plate's top face, named by typing
    _pick_body(page, "base_plate")
    page.click("#part-add-mount")
    page.wait_for_selector("#part-pick-hint")
    pos = page.evaluate("__scrimmagePart.screenPos('Climber/base_plate', {along: [0, 0, 1]})")
    assert pos, "the base plate's top is not on screen"
    page.mouse.click(pos["x"], pos["y"])
    page.wait_for_function("document.activeElement && document.activeElement.id === 'part-mount-name'")
    assert page.evaluate("__scrimmagePart.scene.mounts") == 1     # drawn before it is named
    page.keyboard.press("Control+A")
    page.keyboard.type("top")
    _edit(page, lambda: page.keyboard.press("Enter"))
    m = _body(page, "base_plate")["mounts"]
    assert [x["name"] for x in m] == ["top"]
    assert m[0]["point"][2] == pytest.approx(0.01, abs=1e-4) and np.allclose(np.abs(m[0]["normal"]), [0, 0, 1], atol=1e-3)
    _wait_file(lambda: next(iter(sorted(imp.parent.glob("*/part.toml"))), None), lambda t: '"top"' in t)
    # the two hooks turn together, the other way round
    for body in ("hook_assy", "hook_assy_2"):
        _pick_body(page, body)
        page.select_option("#part-joint-kind", "hinge")
        _edit(page, lambda: page.click("#part-snap-x"))
    page.click("#part-links summary")
    page.select_option("#part-couple-with", "hook_assy")
    page.fill("#part-couple-ratio", "-1")
    _edit(page, lambda: page.click("#part-couple-add"))
    assert page.evaluate("__scrimmagePart.store.state.couplings") == [{"a": "hook_assy_2", "b": "hook_assy", "ratio": -1}]
    # and hook_assy_2 is pinned to arm_B where the arm is clicked
    page.select_option("#part-loop-with", "arm_B")
    page.click("#part-loop-pick")
    page.wait_for_selector("#part-pick-hint")
    pos = page.evaluate("__scrimmagePart.screenPos('Climber/arm_B')")
    _edit(page, lambda: page.mouse.click(pos["x"], pos["y"]))
    loops = page.evaluate("__scrimmagePart.store.state.loops")
    assert [(x["body1"], x["body2"]) for x in loops] == [("hook_assy_2", "arm_B")]
    p = loops[0]["point"]
    assert 0.229 < p[0] < 0.251 and 0.089 < p[1] < 0.111 and 0.0 < p[2] < 0.82     # on arm_B, in CAD meters


def test_click_the_floor_and_the_center(part_page):
    page, _ = part_page
    page.wait_for_selector("#part-frame-step")
    page.click("#part-frame-floor")
    page.wait_for_selector("#part-pick-hint")
    pos = page.evaluate("__scrimmagePart.screenPos('Climber/base_plate', {along: [0, 0, 1]})")
    _edit(page, lambda: page.mouse.click(pos["x"], pos["y"]))
    f = page.evaluate("__scrimmagePart.store.state.frame")
    assert f["floor"] == pytest.approx(0.01, abs=1e-4)    # the plate's top face, as clicked
    assert f["confirmed"] is False
    page.wait_for_selector("#part-pick-hint", state="hidden")
    page.click("#part-frame-center")
    page.wait_for_selector("#part-pick-hint")
    page.wait_for_function("__scrimmagePart.scene.lowest !== null && __scrimmagePart.scene.lowest < -0.005")
    pos = page.evaluate("__scrimmagePart.screenPos('Climber/base_plate', {along: [0, 0, 1]})")
    _edit(page, lambda: page.mouse.click(pos["x"], pos["y"]))
    c = page.evaluate("__scrimmagePart.store.state.frame.center")
    assert 0 <= c[0] <= 0.3 and 0 <= c[1] <= 0.2 and c != pytest.approx([0.15, 0.1])
    assert page.evaluate("__scrimmagePart.store.state.frame.floor") == pytest.approx(0.01, abs=1e-4)
