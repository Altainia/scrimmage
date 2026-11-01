"""Fingerprints and re-import matching: pieces keep their settings when the CAD file changes."""

from pathlib import Path

import numpy as np
import pytest

from scrimmage.cad.matching import fingerprint, match
from scrimmage.cad.source import Piece, load_source

DATA = Path(__file__).parent / "data" / "cad"


def box(key, size, center):
    sx, sy, sz = (s / 2 for s in size)
    v = np.array([[x, y, z] for x in (-sx, sx) for y in (-sy, sy) for z in (-sz, sz)], dtype=float) + center
    f = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]], dtype=np.int32)
    return Piece(key, key.rsplit("/", 1)[-1].split("#")[0], (), v, f)


def records(pieces):
    return [{"key": p.key, "fp": fingerprint(p)} for p in pieces]


def test_fingerprint_values_and_types():
    fp = fingerprint(box("A/b", (0.1, 0.2, 0.4), (1, 2, 3)))
    assert fp["volume"] == pytest.approx(0.008)
    assert fp["area"] == pytest.approx(2 * (0.02 + 0.04 + 0.08))
    assert fp["extents"] == pytest.approx([0.1, 0.2, 0.4])
    assert fp["center"] == pytest.approx([1, 2, 3])
    assert all(type(x) is float for x in [fp["volume"], fp["area"], *fp["extents"], *fp["center"]])


def test_same_file_matches_every_key():
    s = load_source(DATA / "climber.step")
    m = match(records(s.pieces), s.pieces)
    assert m.pairs == {p.key: p.key for p in s.pieces}
    assert not m.guessed and not m.missing and not m.added


def test_v2_renamed_subassembly_matched_by_shape():
    old, new = load_source(DATA / "climber.step"), load_source(DATA / "climber_v2.step")
    m = match(records(old.pieces), new.pieces)
    assert m.pairs["Climber/hook_assy#2/hook"] == "Climber/hook_assy_right/hook"
    # arm_B grew 100 mm in v2, so this hook moved 100 mm: beyond the 5 mm position rule, it is a guess.
    assert "Climber/hook_assy#2/hook" in m.guessed
    assert "Climber/hook_assy/hook" not in m.guessed         # same key and place


def test_removed_bolt_is_missing_and_lengthened_arm_still_matches_by_key():
    old, new = load_source(DATA / "climber.step"), load_source(DATA / "climber_v2.step")
    m = match(records(old.pieces), new.pieces)
    assert m.missing == ["Climber/bolt#6"]
    assert m.added == []
    assert m.pairs["Climber/arm_B"] == "Climber/arm_B"


def test_identical_hooks_keep_their_sides():
    left = box("A/hook", (0.02, 0.02, 0.1), (-0.3, 0, 0))
    right = box("A/hook#2", (0.02, 0.02, 0.1), (0.3, 0, 0))
    old = records([left, right])
    new = [box("B/hook", (0.02, 0.02, 0.1), (0.3, 0, 0)), box("B/hook#2", (0.02, 0.02, 0.1), (-0.3, 0, 0))]
    m = match(old, new)
    assert m.pairs == {"A/hook": "B/hook#2", "A/hook#2": "B/hook"}
    assert not m.guessed


def test_shape_only_match_is_guessed():
    old = records([box("A/hook", (0.02, 0.02, 0.1), (0, 0, 0))])
    new = [box("A/claw", (0.02, 0.02, 0.1), (0.05, 0, 0))]
    m = match(old, new)
    assert m.pairs == {"A/hook": "A/claw"}
    assert m.guessed == {"A/hook"}


def test_different_shape_is_missing_and_added():
    m = match(records([box("A/x", (0.1, 0.1, 0.1), (0, 0, 0))]), [box("A/y", (0.2, 0.1, 0.1), (0, 0, 0))])
    assert m.pairs == {} and m.missing == ["A/x"] and m.added == ["A/y"]


def _turn(p, axis, deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    r = {"x": [[1, 0, 0], [0, c, -s], [0, s, c]], "y": [[c, 0, s], [0, 1, 0], [-s, 0, c]],
         "z": [[c, -s, 0], [s, c, 0], [0, 0, 1]]}[axis]
    return Piece(p.key, p.name, p.group, p.v @ np.array(r).T, p.f)


def _cylinder(key, radius, length, n=48):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = np.stack([radius * np.cos(t), radius * np.sin(t)], axis=1)
    v = np.concatenate([np.c_[ring, np.full(n, -length / 2)], np.c_[ring, np.full(n, length / 2)],
                        [[0, 0, -length / 2], [0, 0, length / 2]]])
    f = []
    for i in range(n):
        j = (i + 1) % n
        f += [[i, j, n + j], [i, n + j, n + i], [2 * n, j, i], [2 * n + 1, n + i, n + j]]
    return Piece(key, key, (), v, np.array(f, dtype=np.int32))


@pytest.mark.parametrize("make", [lambda: box("A/tube", (0.02, 0.02, 0.1), (0.3, 0.2, 0.1)),
                                  lambda: _cylinder("A/bolt", 0.01, 0.1)])
def test_extents_do_not_depend_on_turning(make):
    base = make()
    ref = fingerprint(base)["extents"]
    for axis in "xyz":
        for deg in (0, 20, 45):
            turned = _turn(base, axis, deg)
            assert fingerprint(turned)["extents"] == pytest.approx(ref, rel=0.02)
            assert match(records([base]), [turned]).pairs == {base.key: base.key}


def test_open_mesh_does_not_crash_and_volume_is_not_negative():
    p = box("A/open", (0.1, 0.1, 0.1), (0, 0, 0))
    p = Piece(p.key, p.name, p.group, p.v, p.f[:-2])      # a hole in the surface
    fp = fingerprint(p)
    assert fp["volume"] >= 0 and fp["area"] > 0
    inside_out = box("A/flipped", (0.1, 0.1, 0.1), (0, 0, 0))
    assert fingerprint(Piece("A/flipped", "flipped", (), inside_out.v, inside_out.f[:, ::-1]))["volume"] == \
        pytest.approx(0.001)
    assert match(records([p]), [p]).pairs == {p.key: p.key}
