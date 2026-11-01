"""Run code inside headless FreeCAD for tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scrimmage import freecad_tools

PREFIX = "SCRIMMAGE_JSON:"

needs_freecad = pytest.mark.freecad


def freecad_available() -> bool:
    try:
        return os.environ.get("SCRIMMAGE_SKIP_FREECAD") != "1" and freecad_tools.find_freecad() is not None
    except freecad_tools.FreeCADError:
        return False


def run_in_freecad(code: str, files: dict | None = None, timeout: float = 180) -> dict:
    """Run `code` in FreeCAD (add-on on sys.path; WORK is a scratch dir holding `files`, name -> str/bytes).
    The code prints one `SCRIMMAGE_JSON:{...}` line; return it parsed. Raises FreeCADError with the output on failure."""
    proc = freecad_tools.run_freecad(code, [], timeout, files=files)
    for line in reversed(proc.stdout.splitlines()):
        i = line.find(PREFIX)   # FreeCAD 1.1.3's progress output ("(100 %)") can precede it on the same line
        if i >= 0:
            return json.loads(line[i + len(PREFIX):])
    raise freecad_tools.FreeCADError(f"no {PREFIX} line in FreeCAD output:\n{proc.stdout}\n{proc.stderr}")


FIXTURES_ROOT_NAME = "scrimmage-fixtures"


def _fixtures_root() -> Path:
    # session fixtures outlive one FreeCAD run, so they need a directory the (snap-confined) FreeCAD can read and write
    return freecad_tools._work_root(freecad_tools.find_freecad()).parent / FIXTURES_ROOT_NAME


@pytest.fixture(scope="session")
def fixtures_dir():
    """Build every headless-FreeCAD fixture assembly once per session; returns {name: path to .FCStd}."""
    import shutil
    import uuid

    if not freecad_available():
        pytest.skip("no headless FreeCAD")
    src = (Path(__file__).parent / "freecad" / "make_fixtures.py").read_text(encoding="utf-8")
    root = _fixtures_root()
    session = root / uuid.uuid4().hex
    session.mkdir(parents=True)
    try:
        out = run_in_freecad(
            "import json, os, sys\n"
            "exec(compile(open(os.path.join(WORK, 'make_fixtures.py'), encoding='utf-8').read(), 'make_fixtures.py', 'exec'), "
            "globals())\n"
            f"D = {str(session)!r}\n"
            "paths = {}\n"
            "for n in NAMES:\n"
            "    paths[n] = os.path.join(D, n + '.FCStd')\n"
            "    build(n, paths[n])\n"
            "print('SCRIMMAGE_JSON:' + json.dumps(paths))\n",
            files={"make_fixtures.py": src}, timeout=300)
        yield out
    finally:
        shutil.rmtree(session, ignore_errors=True)
        try:
            root.rmdir()
        except OSError:
            pass
