"""Every browser test closes its contexts through browser_harness.close_context, which drops the routes first: a
route handler still running when its context closes otherwise fails the next test's new_context with "Response has
been disposed" (a CI flake seen on PRs #21 and #24)."""
import re
from pathlib import Path

TESTS = Path(__file__).parent


def test_no_test_closes_a_browser_context_directly():
    bare = re.compile(r"^\s*(ctx|c|context)\.close\(\)", re.M)
    found = [f"{f.name}:{t.count(chr(10), 0, m.start()) + 1}" for f in sorted(TESTS.glob("*.py"))
             if f.name != "browser_harness.py" for t in [f.read_text(encoding="utf-8")] for m in bare.finditer(t)]
    assert not found, f"close these with browser_harness.close_context(ctx): {found}"
