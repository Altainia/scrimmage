"""Every example test file under examples/ opens in the Test tab with no problems and no hints (palette.problems and
palette.hints on the world its runs use): an example a student copies shouldn't start with a note to fix."""

from pathlib import Path

import pytest

from scrimmage.blocks.palette import hints, problems
from scrimmage.blocks.program import load
from scrimmage.blocks.run import world_for

ROOT = Path(__file__).resolve().parent.parent
TESTS = sorted(p for p in (ROOT / "examples").rglob("*.test.toml") if "bench" not in p.parts)


def test_there_are_example_tests():
    assert len(TESTS) >= 5, TESTS


@pytest.mark.parametrize("path", TESTS, ids=lambda p: str(p.relative_to(ROOT / "examples")))
def test_an_example_test_opens_with_no_problems_and_no_hints(path):
    program = load(path)
    world = world_for(program)
    assert problems(program, world) == []
    assert hints(program, world) == []
