"""Every bench example loads, runs, puts a piece out, and creates no energy (spec check 4)."""

import tomllib
from pathlib import Path

import pytest

from scrimmage.bench.run import run_bench
from scrimmage.bench.spec import load_bench, parse_bench, with_overrides

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "bench"
FILES = sorted(EXAMPLES.rglob("*.toml"))
MAX_PIECES = 2  # per inlet, to keep the tests quick; the files' own counts are for the room


def test_the_examples_are_there():
    names = {str(f.relative_to(EXAMPLES)) for f in FILES}
    assert {"hooded.toml", "pinch_car.toml", "bounce_bar.toml", "curved_ramp.toml", "hood_shapes/round.toml",
            "hood_shapes/flared.toml", "hood_shapes/long_wrap.toml"} <= names


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(EXAMPLES)))
def test_examples_run(path):
    bench = load_bench(path)
    assert bench.author and bench.idea  # a student's idea, in their words
    raw = with_overrides(bench.raw, {f"inlet.{i.name}.count": min(i.count, MAX_PIECES) for i in bench.inlets})
    result = run_bench(parse_bench(raw, path.parent))
    assert any(p.status == "out" for p in result.pieces), [p.status for p in result.pieces]
    assert result.energy and all(e.ok for e in result.energy), result.energy


def test_hood_shapes_share_the_launcher():
    """The three hoods differ only in the hood: same piece, shaft, wheel, inlet and outlet, ready for compare."""
    files = sorted((EXAMPLES / "hood_shapes").glob("*.toml"))
    assert len(files) == 3
    raws = [tomllib.loads(f.read_text()) for f in files]
    for key in ("piece", "shaft", "wheel", "inlet", "outlet", "run"):
        assert all(r[key] == raws[0][key] for r in raws), key
    hoods = [next(c for c in r["curve"] if c["name"] == "hood") for r in raws]
    assert len({repr(sorted(h.items())) for h in hoods}) == 3
