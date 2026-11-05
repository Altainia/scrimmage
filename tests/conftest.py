import os

from scrimmage._gl import set_default

set_default()  # before any mujoco import (scrimmage does the same): GLFW aborts on a Wayland desktop

import pytest


def pytest_configure(config):
    # every editor the tests start (in this process or as `scrimmage edit` subprocesses) keeps its recent list in a
    # scratch file, never the user's own start page
    if "SCRIMMAGE_RECENT" not in os.environ:
        import tempfile
        os.environ["SCRIMMAGE_RECENT"] = os.path.join(tempfile.mkdtemp(prefix="scrimmage-recent-"), "recent.json")
    if "SCRIMMAGE_FOLDERS" not in os.environ:    # likewise the team folders ({cad}, {code}) set on this computer
        import tempfile
        os.environ["SCRIMMAGE_FOLDERS"] = os.path.join(tempfile.mkdtemp(prefix="scrimmage-folders-"), "folders.json")
    # likewise the user settings (Units): never the user's own; each test then gets a fresh folder (_settings below)
    import tempfile
    os.environ["SCRIMMAGE_CONFIG"] = tempfile.mkdtemp(prefix="scrimmage-config-")
    config.addinivalue_line("markers", "java: needs the WPILib JDK and Gradle (skipped when SCRIMMAGE_SKIP_JAVA=1)")
    config.addinivalue_line("markers", "network: downloads from the internet (skips itself if the download fails)")
    config.addinivalue_line("markers", "freecad: needs headless FreeCAD (skipped when SCRIMMAGE_SKIP_FREECAD=1 or none is found)")
    config.addinivalue_line("markers", "browser: drives the assembler in headless Chromium via Playwright (skipped when SCRIMMAGE_SKIP_BROWSER=1, or without playwright / a Chromium)")
    config.addinivalue_line("markers", "freecad_gui: opens the FreeCAD GUI on this desktop (skipped unless SCRIMMAGE_FREECAD_GUI=1)")


def pytest_collection_modifyitems(config, items):
    if os.environ.get("SCRIMMAGE_SKIP_JAVA") == "1":
        skip = pytest.mark.skip(reason="SCRIMMAGE_SKIP_JAVA=1")
        for item in items:
            if "java" in item.keywords:
                item.add_marker(skip)
    if os.environ.get("SCRIMMAGE_FREECAD_GUI") != "1":
        skip = pytest.mark.skip(reason="opens a FreeCAD window; set SCRIMMAGE_FREECAD_GUI=1 to run it")
        for item in items:
            if "freecad_gui" in item.keywords:
                item.add_marker(skip)
    if os.environ.get("SCRIMMAGE_SKIP_FREECAD") == "1" or _no_freecad():
        skip = pytest.mark.skip(reason="SCRIMMAGE_SKIP_FREECAD=1 or no FreeCAD binary found")
        for item in items:
            if "freecad" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def _settings(tmp_path_factory, monkeypatch):
    """Every test starts with no settings file (so the product default, imperial, applies) and no `--units`
    override. A server or CLI the test starts inherits $SCRIMMAGE_CONFIG."""
    from scrimmage import settings
    monkeypatch.setenv("SCRIMMAGE_CONFIG", str(tmp_path_factory.mktemp("config")))
    settings.set_override(None)
    yield
    settings.set_override(None)


@pytest.fixture
def units():
    """units("metric") sets the Units setting for this test (in its own settings file, so servers and CLIs the
    test starts see it too)."""
    from scrimmage import settings
    return lambda system: settings.save(units=system)


def _no_freecad() -> bool:
    from scrimmage import freecad_tools
    try:
        return freecad_tools.find_freecad() is None
    except freecad_tools.FreeCADError:
        return True


from freecad_harness import fixtures_dir  # noqa: E402,F401  (session fixture; imported so every test module sees it)
