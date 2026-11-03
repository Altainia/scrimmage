"""Piece kinds (scrimmage/testrange/pieces.py): shapes, hollow slices, CAD pieces, inertia, tip and turn, words."""

import math
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

from scrimmage.testrange.elements import IN
from scrimmage.testrange.pieces import (
    FLOOR_DROP,
    MAX_EXTENT,
    TUBE_SLICES,
    ball_kind,
    disc_kind,
    display_name,
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
