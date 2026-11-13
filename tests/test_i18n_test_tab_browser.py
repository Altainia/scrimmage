"""The Test tab in another language, in headless Chromium (sub-project 20b, Task 4; spec L7): the blocks' messages,
tooltips and pickers, the flyout and the page's own words follow the Language setting live; the blocks are made again
from the workspace's saved state, so a test nobody edits is written byte for byte as it was, and a number being typed
in a block's editor is committed as typed (Review Focus 3).

The catalogs are the tests' own, swapped in through the settings answer (test_i18n_pages_browser.py's catalog()), so
they can change words en-GB spells the same. The fixtures live in tests/test_test_browser.py."""
import pytest

from test_assembler_browser import browser  # noqa: F401  (fixture)
from test_i18n_pages_browser import catalog, english
from test_test_browser import (READY, SPIN, UNIT_BLOCKS, _click_field, _defenses_test, _file_blocks, _wait_file,
                               test_page)  # noqa: F401  (fixtures)

pytestmark = pytest.mark.browser

BLOCKS = {
    "spin %1 at %2 V": "run %1 at %2 volts",
    "wait %1 s": "pause for %1 s",
    "Run a motor at this many volts until a later block changes it.": "Runs a motor until a later block changes it.",
    "blocks\u0004Move": "Motion",
    "move %1 to %2 %3": "take %1 to %2 %3",
    "above": "over",
    "stored": "kept",
    "{count} block in the test|{count} blocks in the test": {"one": "{count} block", "other": "{count} blocks"},
    "These blocks aren't joined to the test, so they don't run: drag them under the others, or delete them.":
        "Loose blocks don't run: join them to the test.",
}
TEXT = "__scrimmageTest.workspace.getTopBlocks(true)[0].toString()"
SPIN_BLOCK = "__scrimmageTest.workspace.getTopBlocks(true)[0]"


def test_the_blocks_and_the_page_follow_the_language(test_page):
    page, path = test_page()
    before = path.read_bytes()
    saved = page.evaluate("__scrimmageTest.state.test")
    assert page.evaluate(TEXT).startswith("spin arm.pivot at 2 V")
    catalog(page, BLOCKS)
    page.wait_for_function(f"{TEXT}.startsWith('run arm.pivot at 2 volts')")
    assert page.evaluate(f"{SPIN_BLOCK}.getNextBlock().toString()").startswith("pause for ")
    assert page.evaluate(f"{SPIN_BLOCK}.getTooltip()") == "Runs a motor until a later block changes it."
    # the flyout: its labels and blocks in the new words
    flyout = page.evaluate("__scrimmageTest.workspace.getFlyout().getWorkspace().getTopBlocks(false).map((b) => b.toString())")
    assert any(t.startswith("run ") for t in flyout), flyout
    assert "Motion" in page.inner_text("#test-blockly")
    assert page.inner_text("#test-count") == "2 blocks"
    # a loose block: this page's own problem, in the new words
    page.evaluate("Blockly.serialization.blocks.append({type: 'wait', fields: {SECONDS: 3}, x: 400, y: 300}, __scrimmageTest.workspace)")
    page.wait_for_function("document.querySelector('#test-problems').textContent.includes('Loose blocks')")
    # nothing was written: the test is unchanged
    page.evaluate("__scrimmageTest.workspace.getTopBlocks(true).filter((b) => !b.getPreviousBlock() && b.type === 'wait' && b.getFieldValue('SECONDS') === 3).forEach((b) => b.dispose())")
    page.wait_for_timeout(800)
    assert page.evaluate("__scrimmageTest.toJSON()") == saved
    assert path.read_bytes() == before
    english(page)
    page.wait_for_function(f"{TEXT}.startsWith('spin arm.pivot at 2 V')")
    assert page.inner_text("#test-count") == "2 blocks in the test"


def test_a_rebuild_keeps_the_files_own_numbers(test_page, tmp_path):
    """A move in meters, a drive and a pressure check shown in the Units setting: after a switch the blocks show the
    same numbers in the new words, and the file is written as it was (block.data's DISPLAY survives)."""
    page, path = test_page(path=_defenses_test(tmp_path, UNIT_BLOCKS))
    before = path.read_bytes()
    saved = page.evaluate("__scrimmageTest.state.test")
    move = "__scrimmageTest.workspace.getAllBlocks(false).find((b) => b.type === 'move')"
    pressure = "__scrimmageTest.workspace.getAllBlocks(false).find((b) => b.type === 'cond_pressure')"
    shown = page.evaluate(f"[({move}).getFieldValue('TO'), ({pressure}).getFieldValue('PSI')]")
    catalog(page, BLOCKS)
    page.wait_for_function(f"({move}).toString().startsWith('take ')")
    assert "kept air pressure over" in page.evaluate(f"({pressure}).toString()")
    assert page.evaluate(f"[({move}).getFieldValue('TO'), ({pressure}).getFieldValue('PSI')]") == shown
    assert page.evaluate("__scrimmageTest.toJSON()") == saved
    page.wait_for_timeout(1000)
    assert path.read_bytes() == before


def test_a_number_being_typed_in_a_block_when_the_language_changes_is_kept(test_page):
    """Review Focus 3: the spin's editor is open with "7" typed when the language changes: it is committed as typed
    (and saved), the block is made again in the new words showing 7, and the editor is closed."""
    page, path = test_page()
    _click_field(page, SPIN_BLOCK, "VOLTS")
    page.wait_for_selector(".blocklyHtmlInput")
    page.keyboard.press("Control+A")
    page.keyboard.type("7")
    catalog(page, BLOCKS)
    page.wait_for_function(f"{TEXT}.startsWith('run arm.pivot at 7 volts')")
    assert page.evaluate(f"{SPIN_BLOCK}.getFieldValue('VOLTS')") == 7
    _wait_file(lambda: _file_blocks(path)[0] == {"do": "spin", "motor": "arm.pivot", "volts": 7.0}, path)
    assert page.locator(".blocklyHtmlInput").count() == 0 or not page.is_visible(".blocklyHtmlInput")
    # the rebuilt blocks still edit and save
    _click_field(page, SPIN_BLOCK, "VOLTS")
    page.wait_for_selector(".blocklyHtmlInput")
    page.keyboard.press("Control+A")
    page.keyboard.type("5")
    page.keyboard.press("Enter")
    _wait_file(lambda: _file_blocks(path)[0]["volts"] == 5.0, path)
    page.evaluate("__scrimmageTest.workspace.undo(false)")
    _wait_file(lambda: _file_blocks(path)[0]["volts"] == 7.0, path)


def test_a_runs_outcome_and_the_runs_list_follow_the_language(test_page):
    """The run's status and outcome words (a key and a code from the server, worded on the page) and its name, after
    the run was shown: drawn again in the new words."""
    from test_test_browser import _run
    page, _ = test_page()
    _run(page, "001")
    assert page.inner_text("#test-result-title") == "Run 1 · finished"
    catalog(page, {"finished": "done", "every block played": "all the blocks ran", "Run {run}": "Go {run}"})
    page.wait_for_function("document.querySelector('#test-result-title').textContent === 'Go 1 · done'")
    assert page.inner_text("#test-outcome") == "All the blocks ran."
    assert page.inner_text("#test-runs").startswith("Go 1 · done")
    english(page)
    page.wait_for_function("document.querySelector('#test-result-title').textContent === 'Run 1 · finished'")


def test_a_kept_spread_follows_the_language_and_units(test_page, tmp_path):
    """Cleanup A1: a spread's table is made in machine text, yet the page shows it in the viewer's Language and Units
    from its table.json, switched live; a spread from before table.json shows table.csv's words as they are."""
    from scrimmage.blocks import store
    from scrimmage.blocks.program import loads
    from scrimmage.blocks.spread import run_spread
    from test_blocks_spread import spread_test
    from test_i18n_pages_browser import switch
    path = spread_test(tmp_path, "offset_in = [0, 30]")
    program = loads(path.read_text(encoding="utf-8"), path)
    base = store.runs_dir(path)
    old = run_spread(program, base)
    (old / "table.json").unlink()                       # as an older scrimmage kept it
    run_spread(program, base)
    page, _ = test_page(path=path)
    page.wait_for_function("__scrimmageTest.replay.runId === 'spread-002/002'")
    cells = "[...document.querySelectorAll('#test-spread-table tbody tr td:first-child')].map((e) => e.textContent)"
    assert page.evaluate(cells) == ["on the center line", "30 in left"]
    assert page.inner_text("#test-spread-sentence") == "Picked up 1 of 2; missed at 30 in left."
    switch(page, "en-GB")
    page.evaluate("import('./units.js').then((m) => m.set('metric'))")
    page.wait_for_function(f"{cells}.join('|') === 'on the centre line|762 mm left'")
    assert page.inner_text("#test-spread-sentence") == "Picked up 1 of 2; missed at 762 mm left."
    # the older spread: table.csv's words, as they are
    page.click("#test-runs [data-run='spread-001/002']")
    page.wait_for_function(f"__scrimmageTest.replay.runId === 'spread-001/002' && {cells}[1] === '30 in left'")
    assert page.inner_text("#test-spread-sentence") == "Picked up 1 of 2; missed at 30 in left."


DRIVE_SHORT = '[[block]]\ndo = "drive"\ndistance = 5.0\ntimeout = 0.3\n'


def test_a_kept_sweep_and_its_run_follow_the_units(test_page, tmp_path):
    """Cleanup A1, A2: a sweep's values, its outcomes and a run kept inside it follow the Units setting live (the
    run's words are result.json's, in every setting); the run made in machine text says meters for a drive."""
    from scrimmage.blocks import store
    from scrimmage.blocks.program import loads
    from scrimmage.blocks.sweep import run_sweep
    page, path = test_page(DRIVE_SHORT)
    run_sweep(loads(path.read_text(encoding="utf-8"), path), {"block": [0], "key": "distance"}, [5.0, 6.0],
              store.runs_dir(path))
    page.wait_for_function("__scrimmageTest.replay.runId === 'sweep-001/002' && __scrimmageTest.sweep")
    values = "[...document.querySelectorAll('#test-sweep-table tbody tr td:first-child')].map((e) => e.textContent)"
    outcomes = "[...document.querySelectorAll('#test-sweep-table .test-sweep-outcome')].map((e) => e.textContent)"
    assert page.evaluate(values) == ["16.404", "19.685"]
    assert page.evaluate(outcomes)[1].startswith("timed out: Drive (block 1) didn't reach 19.69 ft in 0.3 s")
    assert page.inner_text("#test-outcome").startswith("Drive (block 1) didn't reach 19.69 ft in 0.3 s (went ")
    page.evaluate("import('./units.js').then((m) => m.set('metric'))")
    page.wait_for_function(f"{values}.join('|') === '5|6'")
    assert page.evaluate(outcomes)[0].startswith("timed out: Drive (block 1) didn't reach 5 m in 0.3 s")
    page.wait_for_function("document.querySelector('#test-outcome').textContent.startsWith("
                           "\"Drive (block 1) didn't reach 6 m in 0.3 s (went \")")
    assert "didn't reach 6 m" in page.inner_text("#test-runs")


def test_a_sweep_row_whose_soft_note_was_reset_says_so(test_page, tmp_path):
    """K6-R7, task 6 review I1: a sweep row whose run had soft NOTE resets (table.csv's soft_resets column, written
    only then) says so after its outcome, and the column is not shown as a check."""
    import csv
    from scrimmage.blocks import store
    from scrimmage.blocks.program import loads
    from scrimmage.blocks.sweep import run_sweep
    page, path = test_page(DRIVE_SHORT)
    folder = run_sweep(loads(path.read_text(encoding="utf-8"), path), {"block": [0], "key": "distance"}, [5.0, 6.0],
                       store.runs_dir(path))
    with open(folder / "table.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    rows = [rows[0] + ["soft_resets"], rows[1] + ["0"], rows[2] + ["2"]]        # as a run with 2 resets writes it
    with open(folder / "table.csv", "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    page.wait_for_function("__scrimmageTest.replay.runId === 'sweep-001/002' && __scrimmageTest.sweep")
    outcomes = "[...document.querySelectorAll('#test-sweep-table .test-sweep-outcome')].map((e) => e.textContent)"
    page.wait_for_function(f"{outcomes}.length === 2")
    said = page.evaluate(outcomes)
    assert "soft NOTE" not in said[0], said
    assert said[1].endswith(" (a soft NOTE blew up and was reset 2 times, so this row isn't trustworthy)"), said
    heads = "[...document.querySelectorAll('#test-sweep-table thead th')].map((e) => e.textContent)"
    assert page.evaluate(heads) == ["Value", "Outcome"]
