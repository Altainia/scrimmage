"""The 3D view JSON of a compiled bench."""

import json
import tomllib
from pathlib import Path

from scrimmage.assembler.bench_view import bench_view
from scrimmage.bench.build import build_bench
from scrimmage.bench.spec import parse_bench

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "bench"


def _view(name):
    p = EXAMPLES / f"{name}.toml"
    built = build_bench(parse_bench(tomllib.loads(p.read_text()), p.parent))
    return built, bench_view(built)


def test_bounce_bar_shapes_and_boxes():
    built, v = _view("bounce_bar")
    assert {g["instance"] for g in v["geoms"]} == {"wheel.bottom", "wheel.top", "plate.feeder", "bar.kicker"}
    assert {b["instance"] for b in v["boxes"]} == {"inlet.feed", "outlet.goal"}
    assert len(v["geoms"]) == len(built.layout.shape_of_geom)      # no piece or probe geom
    assert set(v["bounds"]) == {"lo", "hi"}
    for g in v["geoms"]:
        assert {"type", "size", "pos", "quat", "rgba", "mesh"} <= set(g)
    json.dumps(v)


def test_hooded_curves_have_several_geoms():
    _, v = _view("hooded")
    for inst in ("curve.flare", "curve.hood"):
        assert sum(g["instance"] == inst for g in v["geoms"]) > 1
