"""Browser tests of the editor's start page (`scrimmage edit` with no FILE), headless Chromium through Playwright.

The fixtures (Chromium lookup, the Server helper) live in tests/test_assembler_browser.py.
"""
import shutil

import pytest

from test_assembler_browser import REPO, Server, browser  # noqa: F401  (fixtures)

pytestmark = pytest.mark.browser


@pytest.fixture
def make_start_page(browser, tmp_path):
    """make_start_page(*extra_args) -> page on a server started in tmp_path with no file (or with the given args)."""
    servers, contexts, logs = [], [], []

    def make(*extra: str):
        srv = Server(None, *extra, cwd=tmp_path)
        servers.append(srv)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        contexts.append(ctx)
        page = ctx.new_page()
        page.set_default_timeout(20000)
        page.on("pageerror", lambda e: logs.append(f"pageerror: {e}"))
        page.on("console", lambda m: logs.append(f"console.{m.type}: {m.text}") if m.type == "error" and "status of 4" not in m.text else None)  # the refused-name test expects a 400
        page.goto(srv.url)
        page.wait_for_function("window.__scrimmage && __scrimmage.mode")
        return page

    yield make
    for c in contexts:
        c.close()
    for s in servers:
        s.stop()
    assert not logs, f"browser errors: {logs}"


def test_start_page_new_session_opens_bench_tab(make_start_page, tmp_path):
    page = make_start_page()
    assert page.evaluate("__scrimmage.mode") == "start"
    assert page.is_visible("#start-page")
    assert not page.is_visible("#tab-btn-robot")
    page.click("[data-key='new-session']")
    page.fill("#start-name", "kickoff")
    page.click("#start-create")
    page.wait_for_function("window.__scrimmage && __scrimmage.mode === 'bench'")
    assert (tmp_path / "kickoff").is_dir()
    assert page.is_visible("#tab-bench")
    assert not page.is_visible("#start-page")
    assert page.is_visible("#start-link")


def test_start_page_recent_and_browse_open_robot(make_start_page, tmp_path):
    shutil.copy(REPO / "configs" / "kitbot.toml", tmp_path / "kitbot.toml")
    page = make_start_page()
    page.click("[data-key='browse']")
    page.click("[data-entry='kitbot.toml']")
    page.wait_for_function("window.__scrimmage && __scrimmage.mode === 'robot' && __scrimmage.state && __scrimmage.scene && __scrimmage.scene.meshes > 0")
    assert page.is_visible("#tab-robot")
    # the Start page link shows the start page again, with the file in the recent list; the server stays in robot mode
    page.click("#start-link")
    assert page.is_visible("#start-page")
    assert not page.is_visible("#tab-robot")
    page.wait_for_selector("[data-recent$='kitbot.toml']")
    page.click("[data-recent$='kitbot.toml']")
    page.wait_for_function("document.getElementById('start-page').hidden")
    page.wait_for_function("window.__scrimmage && __scrimmage.mode === 'robot' && __scrimmage.state && __scrimmage.scene && __scrimmage.scene.meshes > 0")


def test_new_robot_from_start_page(make_start_page, tmp_path):
    page = make_start_page()
    page.click("[data-key='new-robot']")
    page.select_option("#start-template", "swerve")
    page.fill("#start-name", "mybot")
    page.click("#start-create")
    page.wait_for_function("window.__scrimmage && __scrimmage.mode === 'robot' && __scrimmage.state && __scrimmage.state.parts.length === 5")
    assert (tmp_path / "mybot.toml").is_file()


def test_new_scenario_and_errors_as_notices(make_start_page, tmp_path):
    shutil.copy(REPO / "configs" / "kitbot.toml", tmp_path / "kitbot.toml")
    (tmp_path / "taken").mkdir()
    page = make_start_page()
    # an existing folder name is refused in plain words and nothing reloads
    page.click("[data-key='new-session']")
    page.fill("#start-name", "taken")
    page.click("#start-create")
    page.wait_for_selector("#notices .notice-error")
    assert page.evaluate("__scrimmage.mode") == "start"
    page.click("[data-key='new-scenario']")
    page.select_option("#start-robot", label="kitbot.toml")
    page.fill("#start-name", "first")
    page.click("#start-create")
    page.wait_for_function("window.__scrimmage && __scrimmage.mode === 'robot' && __scrimmage.state")
    assert (tmp_path / "first.toml").is_file()
    page.wait_for_function("window.__scrimmage && __scrimmage.tab === 'scenario'")
