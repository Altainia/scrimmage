"""docs/bench-tab.md and the guides that use the Bench tab name only routes, tools, commands and pictures that exist."""

import re
from pathlib import Path

from scrimmage import mcp_server
from scrimmage.assembler import server

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "docs" / "bench-tab.md"
DOCS = [PAGE, ROOT / "docs" / "assembler.md", ROOT / "docs" / "bench.md", ROOT / "README.md",
        *sorted((ROOT / "docs" / "getting-started").glob("*.md"))]
ROUTE = re.compile(r"(/api/(?:app|bench)/\w+)")


def test_page_exists_and_has_screenshot():
    text = PAGE.read_text(encoding="utf-8")
    assert "UNCALIBRATED" in text
    for img in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text):
        assert (PAGE.parent / img).is_file(), img
    assert (ROOT / "docs" / "img" / "bench-tab.png").stat().st_size > 20_000


def test_every_route_named_is_in_the_server_contract():
    contract = server.__doc__
    for md in DOCS:
        for route in ROUTE.findall(md.read_text(encoding="utf-8")):
            assert route in contract, f"{md.name} names {route}, which the server docstring doesn't list"


def test_bench_tools_named_exist():
    tools = {n for n in dir(mcp_server) if n.startswith("bench_")}
    for md in DOCS:
        for name in re.findall(r"`(bench_\w+)`", md.read_text(encoding="utf-8")):
            assert name in tools, f"{md.name} names the tool {name}, which doesn't exist"


def test_the_guides_point_at_the_page():
    for rel in ("docs/getting-started/try-a-mechanism.md", "docs/getting-started/install.md", "docs/bench.md", "README.md"):
        assert "bench-tab.md" in (ROOT / rel).read_text(encoding="utf-8"), rel
    assert "scrimmage edit" in (ROOT / "docs" / "getting-started" / "install.md").read_text(encoding="utf-8")
