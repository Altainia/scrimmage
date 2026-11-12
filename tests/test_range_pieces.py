"""Piece kinds (scrimmage/testrange/pieces.py): shapes, hollow slices, CAD pieces, inertia, tip and turn, words."""

import math
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

from scrimmage.launcher.inputs import PRESETS
from scrimmage.testrange.elements import IN
from scrimmage.testrange.pieces import (
    FLOOR_DROP,
    MAX_EXTENT,
    TUBE_SLICES,
    ball_kind,
    disc_kind,
    display_name,
    kind_preset,
    orientation_words,
    parse_kind,
    rotate,
    solid_mesh,
)
from scrimmage.testrange.spec import RangeError

P = Path("r.toml")
ROOT = Path(__file__).parent
CORAL = {"shape": "tube", "diameter_in": 4.5, "length_in": 11.875, "mass": 0.7, "friction": 0.4}
HOLLOW = {**CORAL, "inner_diameter_in": 4.0}


@pytest.fixture
def hollow():
    return parse_kind(P, "t", HOLLOW, ROOT)


def write_stl(path: Path, v, f) -> None:
    out = [b"\0" * 80, struct.pack("<I", len(f))]
    for a, b, c in f:
        n = np.cross(v[b] - v[a], v[c] - v[a])
        out.append(struct.pack("<12fH", *n, *v[a], *v[b], *v[c], 0))
    path.write_bytes(b"".join(out))


def cube(side: float):
    v = np.array([(x, y, z) for x in (0, side) for y in (0, side) for z in (0, side)], dtype=float)
    f = np.array([(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
                  (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)])
    return v, f


def l_block():
    """An L-shaped prism 0.1 m deep (concave): the L's six corners at z = 0 and z = 0.1."""
    xy = [(0, 0), (0.2, 0), (0.2, 0.05), (0.05, 0.05), (0.05, 0.2), (0, 0.2)]
    v = np.array([(x, y, z) for z in (0.0, 0.1) for x, y in xy], dtype=float)
    cap = [(3, 0, 1), (3, 1, 2), (3, 4, 5), (3, 5, 0)]  # fanned from the inside corner (3)
    f = [(a, c, b) for a, b, c in cap] + [(a + 6, b + 6, c + 6) for a, b, c in cap]
    for i in range(6):
        j = (i + 1) % 6
        f += [(i, j, j + 6), (i, j + 6, i + 6)]
    return v, np.array(f)


def test_a_solid_tube_and_a_hollow_tube(hollow):
    solid = parse_kind(P, "t", CORAL, ROOT)
    assert solid.hole is None and len(solid.solids) == 1 and solid.form == "rigid"
    assert hollow.hole.diameter == pytest.approx(4.0 * IN) and len(hollow.solids) == TUBE_SLICES
    assert hollow.inertia[2] == pytest.approx(0.7 * ((2.25 * IN) ** 2 + (2.0 * IN) ** 2) / 2, rel=0.02)
    assert hollow.extent == pytest.approx(11.875 * IN) and not hollow.flat and hollow.axis == (0, 0, 1)
    assert hollow.mass == 0.7 and hollow.friction == 0.4
    assert hollow.effective_hole_radius() == pytest.approx(2.0 * IN * math.cos(math.pi / TUBE_SLICES))
    assert solid.effective_hole_radius() is None
    for s in hollow.solids:  # every slice is a closed mesh in the kind's frame
        v, f = solid_mesh(s)
        assert s.kind == "mesh" and len(f) >= 4
        assert np.linalg.norm(v[:, :2], axis=1).min() >= 2.0 * IN * math.cos(math.pi / TUBE_SLICES) - 1e-9


def test_orient_and_rest_height(hollow):
    assert hollow.rest_height(hollow.orient(90, 0)) == pytest.approx(11.875 * IN / 2 + FLOOR_DROP, abs=1e-4)
    q = hollow.orient(0, 90)
    assert rotate(q, hollow.axis) == pytest.approx([0, 1, 0], abs=1e-9)
    assert hollow.rest_height(q) == pytest.approx(2.25 * IN + FLOOR_DROP, abs=2e-4)
    assert rotate(hollow.orient(90, 0), hollow.axis) == pytest.approx([0, 0, 1], abs=1e-9)
    assert rotate(hollow.orient(30, 0), hollow.axis) == pytest.approx([math.cos(math.pi / 6), 0, 0.5], abs=1e-9)
    assert hollow.reach(hollow.orient(0, 0)) == pytest.approx(math.hypot(11.875 * IN / 2, 2.25 * IN), abs=1e-4)
    assert hollow.reach(hollow.orient(90, 0)) == pytest.approx(2.25 * IN, abs=1e-6)


def test_flat_kinds_and_a_box_axis():
    note = parse_kind(P, "n", {"shape": "ring", "diameter_in": 14, "inner_diameter_in": 10, "length_in": 2,
                               "mass": 0.235, "friction": 0.6}, ROOT)
    assert note.flat and orientation_words(note, 90, 0) == "lying flat"
    assert orientation_words(note, 0, 0) == "standing on edge"
    assert note.rest_height(note.orient(90, 0)) == pytest.approx(1 * IN + FLOOR_DROP, abs=1e-6)
    tote = parse_kind(P, "b", {"shape": "box", "size_in": [16.9, 26.9, 12.1], "mass": 3.5, "friction": 0.3}, ROOT)
    assert tote.axis == (0, 1, 0) and not tote.flat and [s.kind for s in tote.solids] == ["box"]
    assert tote.extent == pytest.approx(26.9 * IN)
    assert tote.lowest(np.array([1.0, 0, 0, 0])) == pytest.approx(12.1 * IN / 2)
    assert tote.inertia[0] == pytest.approx(3.5 * ((26.9 * IN) ** 2 + (12.1 * IN) ** 2) / 12)


def test_orientation_words(hollow):
    assert [orientation_words(hollow, *a) for a in [(90, 0), (0, 0), (0, 90), (0, -90), (0, 45), (0, 180),
                                                    (40, 90)]] == [
        "standing on end", "lying in line", "lying across", "lying across", "lying at 45°",
        "lying in line, turned around", "leaning 40°, lying across"]
    assert orientation_words(hollow, 85, 30) == "standing on end"
    assert orientation_words(hollow, 5, 270) == "lying across"


def test_a_cone_stands_on_its_base():
    cone = parse_kind(P, "c", {"shape": "cone", "diameter_in": 8, "length_in": 12, "mass": 0.65, "friction": 0.8},
                      ROOT)
    # a full cone's center of mass is a quarter of its height up from the base; its axis runs base to tip
    assert cone.lowest(cone.orient(90, 0)) == pytest.approx(3 * IN, rel=0.02)
    assert cone.lowest(cone.orient(-90, 0)) == pytest.approx(9 * IN, rel=0.02)


def test_a_compound_puts_its_origin_at_the_center_of_mass():
    k = parse_kind(P, "g", {"shape": "compound", "mass": 1.0, "friction": 0.5, "axis": "x", "shapes": [
        {"shape": "box", "size": [0.1, 0.1, 0.1], "pos": [0.3, 0.0, 0.0]},
        {"shape": "box", "size": [0.1, 0.1, 0.1], "pos": [0.5, 0.2, 0.0], "rpy_deg": [0, 0, 45]},
    ]}, ROOT)
    assert np.mean([s.pos for s in k.solids], axis=0) == pytest.approx([0, 0, 0], abs=1e-9)
    assert k.axis == (1, 0, 0) and k.mass == 1.0
    assert k.solids[1].quat == pytest.approx([math.cos(math.pi / 8), 0, 0, math.sin(math.pi / 8)])
    # two 0.5 kg boxes 0.2828 m apart: each box's own inertia plus 0.5 kg at 0.1414 m about the vertical
    d = math.hypot(0.2, 0.2) / 2
    assert k.inertia[2] == pytest.approx(2 * (0.5 * 0.02 / 12 + 0.5 * d * d))


def test_a_compound_with_a_hole():
    k = parse_kind(P, "h", {"shape": "compound", "mass": 1.0, "friction": 0.5, "hole": {"diameter_in": 2},
                            "shapes": [{"shape": "tube", "diameter_in": 6, "inner_diameter_in": 2, "length_in": 1}]},
                   ROOT)
    assert k.hole.diameter == pytest.approx(2 * IN) and k.hole.length == pytest.approx(1 * IN)
    assert k.hole.center == pytest.approx((0, 0, 0), abs=1e-9)


def test_a_cad_piece_from_an_stl(tmp_path):
    write_stl(tmp_path / "cube.stl", *cube(0.1))
    k = parse_kind(P, "c", {"shape": "cad", "cad": "cube.stl", "units": "m", "mass": 0.5, "friction": 0.4}, tmp_path)
    assert k.extent == pytest.approx(0.1) and [s.kind for s in k.solids] == ["mesh"]
    assert k.inertia[0] == pytest.approx(0.5 * 0.02 / 12, rel=1e-4)
    assert k.lowest(np.array([1.0, 0, 0, 0])) == pytest.approx(0.05)


def test_review_focus_5_cad_mistakes(tmp_path, monkeypatch):
    # a 301 mm file read as meters (mm mistaken for m): refused, naming the size
    write_stl(tmp_path / "big.stl", *cube(301.0))
    with pytest.raises(RangeError, match=r"^r\.toml: \[piece\.coral\]: the piece is 301 m across; check units "
                                         r"\(m, mm or in\)$"):
        parse_kind(P, "coral", {"shape": "cad", "cad": "big.stl", "units": "m", "mass": 0.7, "friction": 0.4},
                   tmp_path)
    # past 100 m, whole meters (not 1.23e+03)
    write_stl(tmp_path / "huge.stl", *cube(1234.0))
    with pytest.raises(RangeError, match=r": the piece is 1234 m across; check units \(m, mm or in\)$"):
        parse_kind(P, "coral", {"shape": "cad", "cad": "huge.stl", "units": "m", "mass": 0.7, "friction": 0.4},
                   tmp_path)
    with pytest.raises(RangeError, match=r": the piece is 1234 m across; check units"):
        parse_kind(P, "coral", {"shape": "box", "size": [1234.0, 0.1, 0.1], "mass": 0.7, "friction": 0.4}, tmp_path)
    # a concave piece without coacd: the bench's plain message
    import scrimmage.bench.cad as bench_cad
    import scrimmage.cad.fit as fit
    monkeypatch.setattr(fit, "SHAPED_CACHE", tmp_path / "shaped")
    monkeypatch.setattr(bench_cad, "TEMP_CACHE", tmp_path / "convex")
    monkeypatch.setitem(sys.modules, "coacd", None)
    write_stl(tmp_path / "ell.stl", *l_block())
    with pytest.raises(RangeError, match=r"\[piece\.ell\]: .*concave.*needs coacd"):
        parse_kind(P, "ell", {"shape": "cad", "cad": "ell.stl", "units": "m", "mass": 0.7, "friction": 0.4}, tmp_path)


@pytest.mark.parametrize("table, words", [
    ({**CORAL, "inner_diameter": 0.12}, "inner_diameter 0.12 m must be smaller than diameter 0.1143 m"),
    ({**CORAL, "shape": "pipe"}, "shape must be one of sphere, box, cylinder, tube, ring, cone, compound, cad, not "
                                 "'pipe'"),
    ({k: v for k, v in CORAL.items() if k != "mass"}, "is missing required key 'mass'"),
    ({**CORAL, "diameter": 0.1}, "has both diameter and diameter_in; give one"),
    ({**{k: v for k, v in CORAL.items() if k != "friction"}, "material": "jelly"}, "material 'jelly' isn't a "
                                                                                    "surface"),
    ({**CORAL, "material": "pvc"}, "has both material and friction"),
    ({k: v for k, v in CORAL.items() if k != "friction"}, "needs a surface"),
    ({"shape": "compound", "mass": 1, "friction": 0.5, "axis": "w",
      "shapes": [{"shape": "box", "size": [0.1, 0.1, 0.1]}]}, "axis must be \"x\", \"y\" or \"z\", not 'w'"),
    ({**CORAL, "ball": "2026-fuel"}, "has both ball and shape"),
    ({**CORAL, "size": [1, 1, 1]}, "size isn't used by a tube"),
    ({**CORAL, "diameter_in": 12000}, "the piece is 305 m across; check units (m, mm or in)"),
    ({"shape": "compound", "mass": 1, "friction": 0.5, "shapes": []}, "shapes must list at least one shape"),
    ({"ball": "no-such-ball"}, "unknown ball preset 'no-such-ball'"),
    ({"preset": "no-such-piece"}, "unknown piece preset 'no-such-piece'"),
])
def test_bad_kinds_say_what(table, words):
    with pytest.raises(RangeError) as e:
        parse_kind(P, "coral", table, ROOT)
    assert str(e.value).startswith("r.toml: [piece.coral]: ") and words in str(e.value)


def test_the_inner_diameter_message_word_for_word():
    with pytest.raises(RangeError) as e:
        parse_kind(P, "coral", {**CORAL, "inner_diameter": 0.12}, ROOT)
    assert str(e.value) == "r.toml: [piece.coral]: inner_diameter 0.12 m must be smaller than diameter 0.1143 m"


def test_ball_and_disc_kinds_wrap_their_presets():
    k = ball_kind("fuel", "2026-fuel")
    assert k.form == "ball" and k.extent == pytest.approx(k.ball.diameter) and k.radius == k.ball.diameter / 2
    assert k.rest_height(k.orient(0, 0)) == k.radius and k.preset == "2026-fuel" and k.label == "fuel"
    assert k.inertia[0] == pytest.approx(k.ball.inertia_factor * k.mass * k.radius ** 2)
    d = disc_kind("2013-frisbee", "2013-frisbee", "pc-long")
    assert d.form == "disc" and d.flat and d.radius == d.disc.diameter / 2 and d.aero.name == "pc-long"
    assert d.rest_height(np.array([1.0, 0, 0, 0])) == pytest.approx(d.disc.height / 2)
    assert parse_kind(P, "f", {"ball": "2026-fuel"}, ROOT).ball == k.ball


def test_display_names():
    assert display_name("2025-coral") == "coral" and display_name("2010-soccer-ball") == "soccer ball"
    assert display_name("coral") == "coral" and ball_kind("2026-fuel", "2026-fuel").label == "fuel"
    assert parse_kind(P, "c", {**HOLLOW, "label": "pipe"}, ROOT).label == "pipe"


def test_piece_presets_are_there_with_their_sizes():
    P = PRESETS["piece"]
    assert set(P) == {"2025-coral", "2023-cone", "2023-cube", "2017-gear", "2019-hatch-panel", "2015-tote", "2024-note"}
    coral = kind_preset("2025-coral")
    assert coral.hole is not None and coral.extent == pytest.approx(11.875 * IN, abs=1e-4)
    assert kind_preset("2017-gear").mass == pytest.approx(0.522, abs=0.005) and kind_preset("2017-gear").flat
    assert kind_preset("2019-hatch-panel").hole.diameter == pytest.approx(6 * IN)
    assert kind_preset("2024-note").hole.diameter == pytest.approx(10 * IN) and kind_preset("2024-note").flat
    assert kind_preset("2015-tote").extent == pytest.approx(26.9 * IN)
    assert kind_preset("2015-tote").mass == pytest.approx(3.54, abs=0.01)
    assert kind_preset("2023-cone").mass == pytest.approx(0.652, abs=0.001)
    assert kind_preset("2023-cube").extent == pytest.approx(9.5 * IN)
    for name in P:
        assert 0 < kind_preset(name).friction < 2
        assert kind_preset(name).label == display_name(name)
    assert set(PRESETS["surface"]) == {"pvc", "polycarbonate", "rubber", "foam", "hdpe", "polypropylene", "fabric"}


# ---- CAD units and scale, and a piece far from its expected size (kickoff dry run gap 14) -------------------------

CAD_DATA = ROOT / "data" / "cad"


def test_a_step_file_says_its_own_units():
    from scrimmage.cad.source import step_length_unit
    assert step_length_unit(CAD_DATA / "climber.step") == "mm"


def test_step_units_are_honored(tmp_path):
    # the file says millimeters; units = "in" reads its numbers as inches, 25.4 times bigger (here past 3 m: refused,
    # which shows it was honored: before, units was ignored for a STEP)
    with pytest.raises(RangeError, match=r"the piece is \d+(\.\d+)? m across; check units"):
        parse_kind(P, "c", {"shape": "cad", "cad": str(CAD_DATA / "climber.step"), "units": "in", "mass": 1.0,
                            "friction": 0.4}, tmp_path)


def test_a_cad_piece_far_too_small_is_refused(tmp_path):
    write_stl(tmp_path / "speck.stl", *cube(0.0088))
    with pytest.raises(RangeError, match=r"the piece is only 0.0000088 m across; check units \(m, mm or in\) and scale"):
        parse_kind(P, "c", {"shape": "cad", "cad": "speck.stl", "units": "mm", "mass": 0.2, "friction": 0.4},
                   tmp_path)


def test_a_cad_piece_scaled(tmp_path):
    write_stl(tmp_path / "model.stl", *cube(0.0025))      # a 1:40 scale model
    k = parse_kind(P, "c", {"shape": "cad", "cad": "model.stl", "units": "m", "scale": 40, "mass": 0.5,
                            "friction": 0.4}, tmp_path)
    assert k.extent == pytest.approx(0.1) and k.warnings == ()
    for bad in (0, -2, "big"):
        with pytest.raises(RangeError, match="scale must be"):
            parse_kind(P, "c", {"shape": "cad", "cad": "model.stl", "units": "m", "scale": bad, "mass": 0.5,
                                "friction": 0.4}, tmp_path)
    with pytest.raises(RangeError, match="scale isn't used by a tube"):
        parse_kind(P, "c", {**CORAL, "scale": 2}, tmp_path)


def test_a_tiny_piece_is_warned_about(tmp_path):
    # the official "Game Piece.step" is a 1:40 print: an 8.8 mm NOTE of 0.235 kg
    write_stl(tmp_path / "note.stl", *cube(0.0088))
    k = parse_kind(P, "note", {"shape": "cad", "cad": "note.stl", "units": "m", "mass": 0.2353, "friction": 0.4},
                   tmp_path)
    assert len(k.warnings) == 1
    w = k.warnings[0]
    assert "[piece.note]" in w and "0.0088 m across" in w and "scale" in w


def test_a_piece_heavy_for_its_size_is_warned_about(tmp_path):
    k = parse_kind(P, "brick", {"shape": "box", "size": [0.1, 0.1, 0.1], "mass": 100.0, "friction": 0.4}, tmp_path)
    assert len(k.warnings) == 1 and "100000 kg/m³" in k.warnings[0] and "mass" in k.warnings[0]
    light = parse_kind(P, "fluff", {"shape": "box", "size": [1.0, 1.0, 1.0], "mass": 0.001, "friction": 0.4},
                       tmp_path)
    assert len(light.warnings) == 1 and "0.001 kg/m³" in light.warnings[0]
    assert parse_kind(P, "t", HOLLOW, ROOT).warnings == ()
    assert kind_preset("2024-note").warnings == () and ball_kind("b", "2026-fuel").warnings == ()


def test_a_cad_piece_far_from_waters_density_is_warned_about(tmp_path):
    write_stl(tmp_path / "c.stl", *cube(0.05))
    for mass, density in ((1.5, "12000"), (0.01, "80")):
        k = parse_kind(P, "c", {"shape": "cad", "cad": "c.stl", "units": "m", "mass": mass, "friction": 0.4}, tmp_path)
        assert len(k.warnings) == 1
        w = k.warnings[0]
        assert f"{density} kg/m³" in w and "100" in w and "10000" in w and "scale" in w
    ok = parse_kind(P, "c", {"shape": "cad", "cad": "c.stl", "units": "m", "mass": 0.2, "friction": 0.4}, tmp_path)
    assert ok.warnings == ()
    box = parse_kind(P, "b", {"shape": "box", "size": [0.05, 0.05, 0.05], "mass": 1.5, "friction": 0.4}, tmp_path)
    assert box.warnings == ()


def test_a_ranges_piece_warnings_are_the_ranges(tmp_path):
    from scrimmage.testrange.spec import load_range
    write_stl(tmp_path / "note.stl", *cube(0.0088))
    (tmp_path / "r.toml").write_text('[range]\nname = "t"\n[piece.note]\nshape = "cad"\ncad = "note.stl"\n'
                                     'units = "m"\nmass = 0.2353\nfriction = 0.4\n')
    rng = load_range("r.toml", tmp_path)
    assert any("[piece.note]" in w and "across" in w for w in rng.warnings)


# ---- a STEP's length unit from its unit contexts (review M9) ------------------------------------------------------

def _step(tmp_path, units: list[str], extra: str = "") -> Path:
    """A STEP text with one GLOBAL_UNIT_ASSIGNED_CONTEXT per length unit entity given (enough for step_length_unit)."""
    lines, n = ["ISO-10303-21;", "DATA;"], 10
    for u in units:
        lines += [f"#{n} = {u};", f"#{n + 1} = ( GEOMETRIC_REPRESENTATION_CONTEXT(3) "
                  f"GLOBAL_UNIT_ASSIGNED_CONTEXT((#{n},#900,#901)) REPRESENTATION_CONTEXT('','') );"]
        n += 2
    lines += ["#900 = ( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) );",
              "#901 = ( NAMED_UNIT(*) SI_UNIT($,.STERADIAN.) SOLID_ANGLE_UNIT() );", extra, "ENDSEC;", "END-ISO-10303-21;"]
    p = tmp_path / "u.step"
    p.write_text("\n".join(lines))
    return p


MM = "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) )"
M = "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($,.METRE.) )"
CM = "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.CENTI.,.METRE.) )"
INCH = "( CONVERSION_BASED_UNIT('INCH',#950) LENGTH_UNIT() NAMED_UNIT(#951) )"
FOOT = "( CONVERSION_BASED_UNIT('FOOT',#950) LENGTH_UNIT() NAMED_UNIT(#951) )"


@pytest.mark.parametrize("units, want", [([MM], "mm"), ([M], "m"), ([INCH], "in"), ([CM], "cm"), ([FOOT], "ft"),
                                         ([MM, MM], "mm"), ([MM, INCH], None), ([], None)])
def test_a_step_unit_comes_from_its_unit_contexts(tmp_path, units, want):
    from scrimmage.cad.source import step_length_unit
    assert step_length_unit(_step(tmp_path, units)) == want


def test_a_stray_unit_outside_the_contexts_is_not_the_files(tmp_path):
    # a metre file with a millimetre unit elsewhere (a tolerance's, as some AP242 writers add) is a metre file
    from scrimmage.cad.source import step_length_unit
    stray = "#960 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );"
    assert step_length_unit(_step(tmp_path, [M], stray)) == "m"


def test_a_step_whose_unit_cant_be_told_keeps_loading(tmp_path, monkeypatch):
    # before K1 `units` was ignored on a STEP; one whose unit can't be told still loads, its units ignored, with a
    # warning (not refused)
    import scrimmage.cad.source as source
    monkeypatch.setattr(source, "step_length_unit", lambda path: None)
    k = parse_kind(P, "c", {"shape": "cad", "cad": str(CAD_DATA / "climber.step"), "units": "in", "mass": 1.0,
                            "friction": 0.4}, tmp_path)
    plain = parse_kind(P, "c", {"shape": "cad", "cad": str(CAD_DATA / "climber.step"), "mass": 1.0, "friction": 0.4},
                       tmp_path)
    assert k.extent == pytest.approx(plain.extent)
    assert any("can't tell which units" in w and "ignored" in w for w in k.warnings)


def test_a_centimetre_step_takes_units(tmp_path, monkeypatch):
    import scrimmage.cad.source as source
    monkeypatch.setattr(source, "step_length_unit", lambda path: "cm")
    plain = parse_kind(P, "c", {"shape": "cad", "cad": str(CAD_DATA / "climber.step"), "mass": 1.0, "friction": 0.4},
                       tmp_path)
    k = parse_kind(P, "c", {"shape": "cad", "cad": str(CAD_DATA / "climber.step"), "units": "mm", "mass": 1.0,
                            "friction": 0.4}, tmp_path)
    assert k.extent == pytest.approx(plain.extent / 10.0)


def test_a_rigid_kind_takes_its_own_contact_stiffness():
    """`stiffness` (N/m at one contact, in series with a wheel's tread: launcher/pinch.py) on a shape or a piece
    preset; the 2024 NOTE's is the kickoff dry run's estimate. A ball or a disc has its own and is refused it."""
    assert kind_preset("2024-note").stiffness == 2000.0
    assert parse_kind(P, "t", CORAL, ROOT).stiffness is None
    assert parse_kind(P, "t", {**CORAL, "stiffness": 500}, ROOT).stiffness == 500.0
    assert parse_kind(P, "n", {"preset": "2024-note", "stiffness": 3000}, ROOT).stiffness == 3000.0
    with pytest.raises(RangeError, match="stiffness must be positive"):
        parse_kind(P, "t", {**CORAL, "stiffness": 0}, ROOT)
    with pytest.raises(RangeError, match="stiffness is a rigid piece's contact stiffness; a ball's is its stiffness_n"):
        parse_kind(P, "b", {"ball": "2026-fuel", "stiffness": 1000}, ROOT)


RING = {"shape": "ring", "diameter_in": 14.0, "inner_diameter_in": 10.0, "length_in": 2.0, "mass": 0.235,
        "friction": 0.8}


def test_restitution_is_a_rigid_kinds_key():
    """`restitution` (0 < e < 1, D7) on a ring kind; a ball or a disc has its preset's."""
    assert parse_kind(P, "r", {**RING, "restitution": 0.4}, ROOT).restitution == 0.4
    assert parse_kind(P, "r", RING, ROOT).restitution is None
    for bad in (0, 1, -0.1):
        with pytest.raises(RangeError, match="restitution must be between 0 and 1"):
            parse_kind(P, "r", {**RING, "restitution": bad}, ROOT)
    with pytest.raises(RangeError, match="restitution is its preset's"):
        parse_kind(P, "b", {"ball": "2026-fuel", "restitution": 0.5}, ROOT)
    note = kind_preset("2024-note")
    assert (note.stiffness, note.restitution) == (2000.0, 0.3)
    assert note.soft     # spec D8 (Task 5)


def test_rigid_kinds_keep_their_numbers():
    """Rigid pieces keep every number: restitution is read only for a soft piece."""
    from scrimmage.testrange.build import rigid_contact
    plain = parse_kind(P, "r", RING, ROOT)
    with_e = parse_kind(P, "r", {**RING, "restitution": 0.4}, ROOT)
    assert rigid_contact(with_e) == rigid_contact(plain)
    assert rigid_contact(kind_preset("2024-note")) == rigid_contact(parse_kind(
        P, "n", {"preset": "2024-note", "stiffness": 2000.0}, ROOT))


def test_a_soft_kind_needs_stiffness_and_restitution():
    """F11: `soft = true` needs both keys (a preset's count), names `[piece.<name>]` and the missing key, is for a
    ring, and keeps stiffness in SOFT_STIFFNESS_RANGE."""
    from scrimmage.testrange.pieces import SOFT_STIFFNESS_RANGE
    ok = parse_kind(P, "s", {**RING, "soft": True, "stiffness": 2000, "restitution": 0.3}, ROOT)
    assert ok.soft
    assert parse_kind(P, "n", {"preset": "2024-note", "soft": True}, ROOT).soft
    with pytest.raises(RangeError, match=r"needs restitution: give restitution in \[piece\.s\]"):
        parse_kind(P, "s", {**RING, "soft": True, "stiffness": 2000}, ROOT)
    with pytest.raises(RangeError, match=r"needs stiffness: give stiffness in \[piece\.s\]"):
        parse_kind(P, "s", {**RING, "soft": True, "restitution": 0.3}, ROOT)
    lo, hi = SOFT_STIFFNESS_RANGE
    for bad in (lo - 1, hi + 1):
        with pytest.raises(RangeError, match="stiffness must be between 1000 and 20000"):
            parse_kind(P, "s", {**RING, "soft": True, "stiffness": bad, "restitution": 0.3}, ROOT)
    for good in (lo, hi):
        assert parse_kind(P, "s", {**RING, "soft": True, "stiffness": good, "restitution": 0.3}, ROOT).soft
    with pytest.raises(RangeError, match="soft = true is for a ring piece"):
        parse_kind(P, "t", {**CORAL, "soft": True, "stiffness": 2000, "restitution": 0.3}, ROOT)
    with pytest.raises(RangeError, match="soft must be true or false"):
        parse_kind(P, "s", {**RING, "soft": "yes"}, ROOT)
    with pytest.raises(RangeError, match="soft is for a rigid ring piece"):
        parse_kind(P, "b", {"ball": "2026-fuel", "soft": True}, ROOT)
