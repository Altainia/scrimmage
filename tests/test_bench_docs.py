"""Every bench file in docs/bench.md marked `<!-- bench -->` passes `bench check`, so the docs never teach a file
that doesn't load."""

import re
import tomllib
from pathlib import Path

import pytest

from scrimmage.bench.build import build_bench, check_clear
from scrimmage.bench.spec import parse_bench

DOCS = Path(__file__).resolve().parent.parent / "docs"
BLOCK = re.compile(r"<!-- bench -->\s*\n```toml\n(.*?)\n```", re.S)


def _blocks() -> list[str]:
    return BLOCK.findall((DOCS / "bench.md").read_text(encoding="utf-8"))


def test_docs_have_bench_blocks():
    assert len(_blocks()) >= 3


@pytest.mark.parametrize("i", range(len(_blocks())))
def test_docs_examples_load(i):
    text = _blocks()[i]
    bench = parse_bench(tomllib.loads(text), DOCS)
    check_clear(build_bench(bench))
    assert bench.author and bench.idea  # every docs example is written as a student's idea
