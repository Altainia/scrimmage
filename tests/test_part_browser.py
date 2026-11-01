"""Browser tests of the Part tab (`scrimmage edit climber.step`), headless Chromium through Playwright.

The fixtures (Chromium lookup, the Server helper) live in tests/test_assembler_browser.py. Opening the copied
tests/data/cad/climber.step makes parts/climber.import.toml next to it; its five bodies are base_plate (with six
bolts folded in as hardware), arm_A, arm_B, hook_assy and hook_assy_2.
"""
import shutil
from pathlib import Path

import pytest

from test_assembler_browser import Server, browser  # noqa: F401  (fixtures)

pytestmark = pytest.mark.browser

DATA = Path(__file__).resolve().parent / "data" / "cad"
ARM = "Climber/arm_A"
BOLT = "Climber/bolt"
READY = ("window.__scrimmagePart && __scrimmagePart.store.state && __scrimmagePart.scene"
         " && __scrimmagePart.scene.drawn.length > 0")


@pytest.fixture
def part_page(browser, tmp_path, monkeypatch):
    """(page, the import file) on `scrimmage edit tmp_path/climber.step`, with its own recent list and cache."""
    monkeypatch.setenv("SCRIMMAGE_RECENT", str(tmp_path / "recent.json"))
    monkeypatch.setenv("SCRIMMAGE_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("SCRIMMAGE_FOLDERS", str(tmp_path / "folders.json"))
    shutil.copy(DATA / "climber.step", tmp_path / "climber.step")
    srv = Server(tmp_path / "climber.step", cwd=tmp_path)
    ctx = browser.new_context(viewport={"width": 1500, "height": 950})
    logs = []
    try:
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        page.on("console", lambda m: logs.append(f"console.{m.type}: {m.text}")
                if m.type == "error" and "status of 4" not in m.text else None)
        page.goto(srv.url)
        page.wait_for_function(READY)
        yield page, tmp_path / "parts" / "climber.import.toml"
    finally:
        ctx.close()
        srv.stop()
    assert not logs, f"browser errors: {logs}"


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
