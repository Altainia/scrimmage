"""Default bodies: how a CAD source's pieces are grouped, and how hardware joins the body it sits on."""

from pathlib import Path

import numpy as np

from scrimmage.cad.bodies import default_bodies, is_hardware
from scrimmage.cad.source import Piece, Source, load_source

DATA = Path(__file__).parent / "data" / "cad"


def box(lo, hi):
    lo, hi = np.array(lo, float), np.array(hi, float)
    v = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    return v, np.array([[0, 1, 3], [0, 3, 2]], dtype=np.int32)


def mk(key, lo, hi, group=()):
    v, f = box(lo, hi)
    return Piece(key, key.split("/")[-1], group, v, f)


def test_climber_default_bodies():
    s = load_source(DATA / "climber.step")
    b = default_bodies(s)
    assert set(b) == {"base_plate", "arm_A", "arm_B", "hook_assy", "hook_assy_2"}
    bolt_keys = [p.key for p in s.pieces if p.name == "bolt"]
    assert len(bolt_keys) == 6
    assert all(k in b["base_plate"] for k in bolt_keys)
    assert b["hook_assy_2"] == ["Climber/hook_assy#2/hook"]


def test_hardware_by_name_and_by_size():
    p = mk("x", (0, 0, 0), (1, 1, 1))
    for n in ("bolt", "M5 screw", "Hex_Nut", "washer-2", "91290A115", "standoff"):
        assert is_hardware(Piece(n, n, (), p.v, p.f), 10.0), n
    assert not is_hardware(Piece("boltcutter", "boltcutter", (), p.v, p.f), 10.0)
    assert not is_hardware(p, 10.0)
    tiny = mk("t", (0, 0, 0), (0.05, 0.05, 0.05))
    assert is_hardware(tiny, 10.0) and not is_hardware(tiny, 1.0)


def test_hardware_never_left_out():
    for f in ("climber.step", "stl"):
        s = load_source(DATA / f)
        keys = [k for ks in default_bodies(s).values() for k in ks]
        assert sorted(keys) == sorted(p.key for p in s.pieces)


def test_stl_each_file_a_body():
    s = load_source(DATA / "stl")
    b = default_bodies(s)
    assert len(b) == len(s.pieces)
    assert all(len(v) == 1 for v in b.values())


def test_hardware_nearest_when_outside_and_multiple_groups():
    a = mk("A/plate", (0, 0, 0), (1, 1, 0.1), ("A",))
    c = mk("C/plate", (5, 0, 0), (6, 1, 0.1), ("C",))
    off = mk("bolt", (4.5, 0, 0), (4.51, 0.01, 0.01))
    s = Source("step", Path("x"), "m", [a, c, off], ["A", "C"])
    b = default_bodies(s)
    assert b == {"A": ["A/plate"], "C": ["C/plate", "bolt"]}
