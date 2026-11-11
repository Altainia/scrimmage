"""docs/test-range.md: its TOML examples parse, and its complete example builds a World."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from scrimmage.testrange.spec import BUILTIN_RANGES, load_range
from scrimmage.world import World

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "test-range.md"
FENCE = re.compile(r"(<!-- complete -->\n)?```toml\n(.*?)```", re.S)


def blocks() -> list[tuple[bool, str]]:
    """(marked complete, text) of every toml block in the doc."""
    return [(m.group(1) is not None, m.group(2)) for m in FENCE.finditer(DOC.read_text(encoding="utf-8"))]


def test_docs_range_examples_parse():
    mine = [(done, text) for done, text in blocks() if text.lstrip().startswith(("[range]", "[shooter]"))]
    assert mine, "docs/test-range.md has no [range] or [shooter] example"
    for _, text in mine:
        tomllib.loads(text)


def test_docs_complete_example_builds_a_world():
    complete = [text for done, text in blocks() if done]
    assert len(complete) == 1, "mark exactly one example <!-- complete -->"
    cfg = tomllib.loads(complete[0])
    assert "range" in cfg and "shooter" in cfg
    w = World(cfg, ROOT)
    assert w.test_range is not None and w.test_range.shooter is not None
    assert w.test_range.counts()["held"] == cfg["range"]["preload"]


def test_docs_range_file_example_loads(tmp_path):
    """The range-file example in the doc is a real range file."""
    files = [text for _, text in blocks() if text.lstrip().startswith("# range file")]
    assert len(files) == 1
    (tmp_path / "mine.toml").write_text(files[0], encoding="utf-8")
    rng = load_range("mine.toml", tmp_path)
    assert rng.goals and rng.starts


def test_docs_names_match_the_code():
    """Keys and commands the doc relies on exist."""
    text = DOC.read_text(encoding="utf-8")
    for name in BUILTIN_RANGES:
        assert f'"{name}"' in text
    for word in ("rolling_resistance", "LAUNCH_CLEAR_S", "/MuJoCo/Balls", "by_time", "robot_env", "launcher aim"):
        assert word in text


def test_docs_name_a_pieces_squeeze_keys_and_how_to_measure_them():
    """The `[piece.<name>]` table's `stiffness`, `restitution` and `soft`, and measuring.md's two NOTE tests."""
    text = DOC.read_text(encoding="utf-8")
    for key in ("`stiffness` (N/m)", "`restitution` (above 0", "`soft = true`"):
        assert key in text
    measuring = (ROOT / "docs" / "measuring.md").read_text(encoding="utf-8")
    for heading, key in (("## NOTE squeeze", "stiffness"), ("## NOTE drop", "restitution")):
        assert heading in measuring
        section = measuring.split(heading, 1)[1].split("\n## ", 1)[0]
        assert f"[piece.note] {key}" in section
