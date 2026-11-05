"""The headless Chromium the browser tests share (the `browser` fixture), through Playwright.

The tests that use it are marked `browser`. They skip, with a reason, when `playwright` isn't importable, when no
usable Chromium is found, or when SCRIMMAGE_SKIP_BROWSER=1. With SCRIMMAGE_REQUIRE_BROWSER=1 (set on CI) a missing
playwright or Chromium fails them instead, so CI can't pass with the browser tests silently skipped.

Chromium: CI runs `python -m playwright install --with-deps chromium` for the pinned playwright version (see the
`dev` extra in pyproject.toml), and the tests then use that build. Locally the order is:
  1. SCRIMMAGE_CHROMIUM=<path to a chrome / chrome-headless-shell executable> (always wins);
  2. the build the installed playwright expects, when it is in the cache (~/.cache/ms-playwright, or
     PLAYWRIGHT_BROWSERS_PATH);
  3. the newest cached chromium_headless_shell-* / chromium-* build, whatever its revision (an older or newer
     Chromium is fine for these tests).
WebGL goes through SwiftShader (software), so no GPU is needed.
"""
import glob
import os
import re
from pathlib import Path

import pytest

GL_ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"]


def _find_chromium(sync_playwright) -> str | None:
    env = os.environ.get("SCRIMMAGE_CHROMIUM")
    if env:
        return env if Path(env).is_file() else None
    try:
        with sync_playwright() as p:
            default = p.chromium.executable_path
        if default and Path(default).is_file():
            return default
    except Exception:
        pass
    root = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or Path.home() / ".cache" / "ms-playwright")
    for pattern in ("chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
                    "chromium-*/chrome-linux*/chrome"):
        found = sorted(glob.glob(str(root / pattern)), key=lambda s: int(re.search(r"-(\d+)/", s).group(1)))
        if found:
            return found[-1]
    return None


def _unavailable(reason: str):
    """Skip for a missing browser, or fail when SCRIMMAGE_REQUIRE_BROWSER=1."""
    if os.environ.get("SCRIMMAGE_REQUIRE_BROWSER") == "1":
        pytest.fail(f"SCRIMMAGE_REQUIRE_BROWSER=1 but the browser tests can't run: {reason}")
    pytest.skip(reason)


@pytest.fixture(scope="module")
def browser():
    if os.environ.get("SCRIMMAGE_SKIP_BROWSER") == "1":
        pytest.skip("SCRIMMAGE_SKIP_BROWSER=1")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _unavailable("playwright is not installed (pip install -e '.[dev]')")
    exe = _find_chromium(sync_playwright)
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True, args=GL_ARGS, **({"executable_path": exe} if exe else {}))
        except Exception as e:  # no browser installed, or one that cannot start
            _unavailable(f"no usable Chromium (run `python -m playwright install chromium` or set SCRIMMAGE_CHROMIUM): "
                        f"{str(e).splitlines()[0]}")
        yield b
        b.close()


def page_units(page, system: str) -> None:
    """Set the Units setting from the open page (as the Settings menu does): saved, and applied there at once."""
    assert page.evaluate("async (s) => (await import('./units.js')).set(s)", system) is None
    assert page.evaluate("async () => (await import('./units.js')).units.system") == system
