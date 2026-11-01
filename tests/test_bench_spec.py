"""The bench file: parsing, defaults, errors, includes and overrides."""

import copy
import math
import tomllib
from pathlib import Path

import pytest

from scrimmage.bench.spec import BenchError, load_bench, parse_bench, with_overrides
from scrimmage.launcher.inputs import PRESETS, Ball

PINCH_CAR = """
[bench]
name = "pinch car"
author = "Maya"
idea = "two wheels squeeze a toy car out"

[piece]
name = "car"
kind = "rigid"
shape = "box"
size = [0.10, 0.06, 0.04]
mass = 0.2

[shaft.top]
motor = "KrakenX60"
target_rps = 50
kP = 0.1
wheel_inertia = 0.0005

[shaft.bottom]
motor = "KrakenX60"
target_rps = 50
kP = 0.1
wheel_inertia = 0.0005

[[wheel]]
name = "top"
shaft = "top"
center = [0.0, 0.0, 0.06]
diameter_in = 4
width = 0.04
tread = "35A"

[[wheel]]
name = "bottom"
shaft = "bottom"
center = [0.0, 0.0, -0.01]
diameter_in = 4
width = 0.04

[[inlet]]
name = "feed"
center = [-0.4, 0.0, 0.025]
size = [0.1, 0.1, 0.05]
count = 3
velocity = [2.0, 0.0, 0.0]

[[outlet]]
name = "shot"
center = [1.0, 0.0, 0.025]
size = [0.2, 0.5, 0.5]
"""


def pinch(**edits):
    raw = tomllib.loads(PINCH_CAR)
    for dotted, v in edits.items():
        raw = with_overrides(raw, {dotted: v})
    return raw


def test_minimal_file_parses(tmp_path):
    f = tmp_path / "pinch.toml"
    f.write_text(PINCH_CAR)
    b = load_bench(f)
    assert b.name == "pinch car" and b.author == "Maya"
    assert b.piece.kind == "rigid" and b.piece.shape == "box" and b.piece.size == (0.10, 0.06, 0.04)
    assert b.piece.ball is None and b.piece.units is None
    assert set(b.shafts) == {"top", "bottom"}
    top = b.shafts["top"]
    assert top.motor == "KrakenX60" and top.control == "speed" and top.target_rps == 50.0
    assert top.count == 1 and top.gearing == 1.0
    assert [w.name for w in b.wheels] == ["top", "bottom"]
    assert b.wheels[0].diameter == pytest.approx(0.1016) and b.wheels[0].axis == (0.0, 1.0, 0.0)
    assert b.inlets[0].count == 3 and b.inlets[0].velocity == (2.0, 0.0, 0.0)
    assert b.outlets[0].name == "shot" and b.outlets[0].size == (0.2, 0.5, 0.5)
    assert b.run.duration == 5.0 and b.run.jam_time == 1.0 and b.run.jam_distance == 0.01
    assert "polycarbonate" in b.materials and "35A" in b.materials
    assert b.base_dir == tmp_path


def test_inch_keys():
    tmp_dir = Path(".")
    b = parse_bench(pinch(), tmp_dir)
    assert math.isclose(b.wheels[0].diameter, 4 * 0.0254, rel_tol=1e-12)
    raw = pinch()
    raw["wheel"][0]["diameter"] = 0.1
    with pytest.raises(BenchError, match="diameter.*diameter_in"):
        parse_bench(raw, tmp_dir)
    raw = pinch()
    raw["plate"] = [{"name": "p", "center_in": [1, 2, 3], "size_in": [4, 5, 6]}]
    p = parse_bench(raw, tmp_dir).plates[0]
    assert p.center == pytest.approx((0.0254, 0.0508, 0.0762)) and p.size == pytest.approx((0.1016, 0.127, 0.1524))


def test_unknown_key_lists_valid_ones(tmp_path):
    raw = pinch()
    raw["wheel"][0]["diamter"] = 0.1
    with pytest.raises(BenchError) as e:
        parse_bench(raw, tmp_path)
    msg = str(e.value)
    assert "diamter" in msg and "diameter" in msg and "diameter_in" in msg and "tread" in msg
    with pytest.raises(BenchError, match="unknown section.*wheel"):
        parse_bench({**pinch(), "wheels": []}, tmp_path)


def test_squishy_non_ball_is_an_error(tmp_path):
    raw = pinch()
    raw["piece"]["kind"] = "squishy"
    with pytest.raises(BenchError, match="rigid stand-in"):
        parse_bench(raw, tmp_path)
    raw["piece"]["shape"] = "disc"
    raw["piece"]["size"] = [0.27, 0.02]
    with pytest.raises(BenchError, match="rigid stand-in"):
        parse_bench(raw, tmp_path)


def test_ball_preset_is_squishy(tmp_path):
    raw = pinch()
    raw["piece"] = {"preset": "2026-fuel"}
    p = parse_bench(raw, tmp_path).piece
    assert p.kind == "squishy" and p.shape == "sphere" and p.name == "2026-fuel"
    assert p.ball == Ball(**PRESETS["ball"]["2026-fuel"])
    assert p.size == (p.ball.diameter,) and p.mass == p.ball.mass
    assert p.inertia == pytest.approx((0.4 * p.mass * (p.ball.diameter / 2) ** 2,) * 3)
    assert p.stiffness is None and p.mesh is None


def test_custom_pieces(tmp_path):
    raw = pinch()
    raw["piece"] = {"kind": "rigid", "shape": "disc", "size": [0.27, 0.02], "mass": 0.175, "stiffness": 5000}
    p = parse_bench(raw, tmp_path).piece
    assert p.shape == "cylinder" and p.stiffness == 5000.0 and p.ball is None
    raw["piece"] = {"kind": "rigid", "shape": "mesh", "mesh": "ring.stl", "mass": 0.2, "inertia": [1e-3] * 3}
    with pytest.raises(BenchError, match="units"):
        parse_bench(raw, tmp_path)
    raw["piece"]["units"] = "mm"
    p = parse_bench(raw, tmp_path).piece
    assert p.units == "mm" and p.mesh == tmp_path / "ring.stl"
    # a custom squishy ball without ball numbers borrows the nearest preset, with a warning
    raw["piece"] = {"kind": "squishy", "shape": "sphere", "size": [0.18], "mass": 0.15}
    b = parse_bench(raw, tmp_path)
    p = b.piece
    assert p.ball.stiffness_n == PRESETS["ball"]["2020-power-cell"]["stiffness_n"]
    assert len(b.warnings) == 1 and "2020-power-cell" in b.warnings[0]  # kept for the CLI and MCP to say
    raw["piece"] |= {"stiffness_n": 60.0, "restitution": 0.5}
    assert parse_bench(raw, tmp_path).warnings == ()
    raw["piece"] = {"kind": "squishy", "shape": "sphere", "size": [0.18], "mass": 0.15, "stiffness": 100}
    with pytest.raises(BenchError, match="stiffness_n"):
        parse_bench(raw, tmp_path)


def test_shaft_rules(tmp_path):
    raw = pinch()
    del raw["shaft"]["top"]["motor"]
    with pytest.raises(BenchError, match="target_rps.*motor"):
        parse_bench(raw, tmp_path)
    raw = pinch()
    raw["wheel"][0]["shaft"] = "nope"
    with pytest.raises(BenchError, match="nope.*bottom.*top|nope.*top.*bottom"):
        parse_bench(raw, tmp_path)
    raw = pinch()
    raw["shaft"]["top"]["motor"] = "Nope"
    with pytest.raises(BenchError, match="KrakenX60"):
        parse_bench(raw, tmp_path)
    # control follows from what is given; a roller is free; a coupled shaft follows another
    raw = pinch()
    raw["shaft"]["roller"] = {"wheel_inertia": 1e-4}
    raw["shaft"]["belt"] = {"wheel_inertia": 1e-4, "coupled_to": "top", "ratio": -1.0}
    raw["shaft"]["volts"] = {"motor": "NEO", "voltage": 6.0, "wheel_inertia": 1e-4}
    s = parse_bench(raw, tmp_path).shafts
    assert s["roller"].control == "free" and s["roller"].motor is None
    assert s["belt"].control == "free" and s["belt"].coupled_to == "top" and s["belt"].ratio == -1.0
    assert s["volts"].control == "voltage" and s["volts"].voltage == 6.0
    raw["shaft"]["belt"]["coupled_to"] = "belt"
    with pytest.raises(BenchError, match="itself"):
        parse_bench(raw, tmp_path)


def test_shaft_must_be_tables(tmp_path):
    """[[shaft]] or shaft = 5 is a plain BenchError, with and without an include."""
    for text in ("[[shaft]]\nmotor = 'NEO'\n", "shaft = 5\n", "[shaft]\ntop = 5\n"):
        raw = pinch()
        del raw["shaft"]
        raw |= tomllib.loads(text)
        raw["wheel"] = []
        with pytest.raises(BenchError, match=r"\[shaft\.<name>\] must be a table: write \[shaft\.top\]"):
            parse_bench(raw, tmp_path)
    (tmp_path / "feed.toml").write_text('[piece]\npreset = "2026-fuel"\n')
    for text in ("[[shaft]]\nmotor = 'NEO'\n", "shaft = 5\n"):
        f = tmp_path / "x.toml"
        f.write_text('include = ["feed.toml"]\n' + text + '[bench]\nname = "x"\n')
        with pytest.raises(BenchError, match=r"must be a table: write \[shaft\.top\]"):
            load_bench(f)
    (tmp_path / "bad.toml").write_text("[[shaft]]\nmotor = 'NEO'\n")
    f.write_text('include = ["bad.toml"]\n[bench]\nname = "x"\n')
    with pytest.raises(BenchError, match=r"bad\.toml.*must be a table"):
        load_bench(f)


def curve_raw(**kw):
    raw = pinch()
    raw["curve"] = [{"name": "hood", "center": [0, 0, 0], "radius_start": 0.1, "radius_end": 0.1,
                     "start_deg": 0, "end_deg": 180, "width": 0.05, "thickness": 0.003, **kw}]
    return raw


def test_curve_checks(tmp_path):
    c = parse_bench(curve_raw(), tmp_path).curves[0]
    assert c.axis == (0.0, 1.0, 0.0) and abs(math.dist(c.zero_dir, (0, 0, 0)) - 1) < 1e-12
    assert math.isclose(parse_bench(curve_raw(radius_start=0.2), tmp_path).curves[0].radius_end, 0.1)
    for bad, msg in [({"end_deg": 400}, "360"), ({"end_deg": 0}, "span"), ({"width": 0.0}, "width"),
                     ({"radius_start": 0.0}, "radius_start"), ({"radius_end": -0.1}, "radius_end"),
                     ({"radius_start": 0.002}, "thickness")]:
        with pytest.raises(BenchError, match=msg):
            parse_bench(curve_raw(**bad), tmp_path)
    raw = curve_raw()
    raw["curve"][0]["material"] = "wood"
    with pytest.raises(BenchError, match="wood.*polycarbonate"):
        parse_bench(raw, tmp_path)


def test_include_merges_and_clashes(tmp_path):
    (tmp_path / "feed.toml").write_text(
        '[piece]\npreset = "2026-fuel"\n[[inlet]]\nname = "feed"\ncenter = [0,0,0]\nsize = [0.1,0.1,0.1]\n'
        '[[outlet]]\nname = "shot"\ncenter = [1,0,0]\nsize = [1,1,1]\n')
    (tmp_path / "other.toml").write_text(
        '[[outlet]]\nname = "shot"\ncenter = [2,0,0]\nsize = [1,1,1]\n')
    (tmp_path / "cad").mkdir()
    (tmp_path / "cad" / "hood.toml").write_text(
        '[[cad]]\nname = "hood"\nmesh = "hood.stl"\nunits = "in"\ncenter = [0,0,0]\n')
    (tmp_path / "sub.toml").write_text('include = ["cad/hood.toml"]\n')
    own = '[bench]\nname = "x"\n[[outlet]]\nname = "shot"\ncenter = [5,0,0]\nsize = [1,1,1]\n'
    (tmp_path / "x.toml").write_text('include = ["feed.toml", "sub.toml"]\n' + own)
    b = load_bench(tmp_path / "x.toml")
    assert b.piece.name == "2026-fuel" and [i.name for i in b.inlets] == ["feed"]
    assert [o.center for o in b.outlets] == [(5.0, 0.0, 0.0)]  # the file's own entry wins
    assert b.cads[0].mesh == tmp_path / "cad" / "hood.stl"  # relative to the file that names it
    assert "include" not in b.raw
    assert parse_bench(b.raw, tmp_path).outlets == b.outlets
    (tmp_path / "y.toml").write_text('include = ["feed.toml", "other.toml"]\n[bench]\nname = "y"\n')
    with pytest.raises(BenchError, match=r"shot.*feed\.toml.*other\.toml"):
        load_bench(tmp_path / "y.toml")
    (tmp_path / "loop.toml").write_text('include = ["loop.toml"]\n[bench]\nname = "l"\n')
    with pytest.raises(BenchError, match="itself|cycle|loop"):
        load_bench(tmp_path / "loop.toml")


def test_overrides_address_entries_by_name(tmp_path):
    raw = pinch()
    out = with_overrides(raw, {"wheel.top.diameter": 0.08, "shaft.top.target_rps": 30, "run.duration": 2,
                               "wheel.bottom.diameter_in": 3})
    assert raw == pinch()  # not changed in place
    b = parse_bench(out, tmp_path)
    assert b.wheels[0].diameter == 0.08 and b.shafts["top"].target_rps == 30.0 and b.run.duration == 2.0
    assert math.isclose(b.wheels[1].diameter, 3 * 0.0254)  # the _in form replaced the other
    assert math.isclose(parse_bench(with_overrides(raw, {"wheel.top.diameter": 0.08}), tmp_path).wheels[0].diameter,
                        0.08)  # and the plain form replaces a stored _in
    for bad in ("wheel.mid.diameter", "wheel.top.diamter", "wheels.top.diameter", "wheel.diameter", "run.x.y"):
        with pytest.raises(BenchError):
            with_overrides(raw, {bad: 1})
    with pytest.raises(BenchError, match="no wheel named 'mid'.*bottom.*top|no wheel named 'mid'.*top.*bottom"):
        with_overrides(raw, {"wheel.mid.diameter": 1})
    c = with_overrides(curve_raw(), {"curve.hood.radius_end": 0.2})
    assert parse_bench(c, tmp_path).curves[0].radius_end == 0.2


def test_sweep_and_charts(tmp_path):
    raw = pinch()
    raw["sweep"] = {"wheel.top.diameter": [0.08, 0.1]}
    raw["chart"] = [{"x": "wheel.top.diameter"}]
    b = parse_bench(raw, tmp_path)
    assert b.sweep == {"wheel.top.diameter": [0.08, 0.1]} and b.charts == [{"x": "wheel.top.diameter"}]
    raw["sweep"] = {"wheel.nope.diameter": [1]}
    with pytest.raises(BenchError, match="nope"):
        parse_bench(raw, tmp_path)


def test_cross_references(tmp_path):
    raw = pinch()
    raw["flight"] = {"outlet": "shot", "target_distance": 4.0, "target_height": 2.0}
    f = parse_bench(raw, tmp_path).flight
    assert f.outlet == "shot" and f.air == "default" and f.ring_diameter is None
    raw["flight"]["outlet"] = "nowhere"
    with pytest.raises(BenchError, match="nowhere.*shot"):
        parse_bench(raw, tmp_path)
    raw = pinch()
    raw["cad"] = [{"name": "c", "part": "p", "slots": {"A": "ghost"}}]
    with pytest.raises(BenchError, match="ghost"):
        parse_bench(raw, tmp_path)
    raw["cad"] = [{"name": "c", "mesh": "a.stl"}]
    with pytest.raises(BenchError, match="units"):
        parse_bench(raw, tmp_path)
    raw = pinch()
    raw["bar"] = [{"name": "b", "a": [0, 0, 0], "b": [0, 0.1, 0], "diameter": 0.01, "spring": 2000}]
    with pytest.raises(BenchError, match="travel"):
        parse_bench(raw, tmp_path)
    raw["bar"][0]["travel"] = 0.01
    bar = parse_bench(raw, tmp_path).bars[0]
    assert bar.spring == 2000.0 and bar.spring_axis == (0.0, 0.0, 1.0)
    raw["wheel"].append(copy.deepcopy(raw["wheel"][0]))
    with pytest.raises(BenchError, match="twice|duplicate"):
        parse_bench(raw, tmp_path)
