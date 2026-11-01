"""CAD on the bench: mesh files (STL or OBJ, in their own units, concave ones split into convex pieces) and part
folders from the FreeCAD exporter (bodies welded to the bench, a motor slot's joint turned by a bench shaft)."""

import importlib.util
import math
import struct
import sys
import time
import tomllib
import warnings
from pathlib import Path

import mujoco
import numpy as np
import pytest

from scrimmage.bench import cad
from scrimmage.bench.build import build_bench, check_clear
from scrimmage.bench.run import run_bench
from scrimmage.bench.spec import IN, BenchError, parse_bench, with_overrides

ROOT = Path(__file__).resolve().parent.parent
HOODED = ROOT / "examples" / "bench" / "hooded.toml"
HAVE_COACD = importlib.util.find_spec("coacd") is not None

HEAD = """
[bench]
name = "cad"
author = "Maya"
idea = "a CAD part on the bench"

[piece]
name = "car"
kind = "rigid"
shape = "box"
size = [0.10, 0.06, 0.04]
mass = 0.2
"""


def cube(lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
    v = np.array([(x, y, z) for x in (lo, hi) for y in (lo, hi) for z in (lo, hi)], dtype=float)
    f = np.array([(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),
                  (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)])
    return v, f


def write_stl(path: Path, v: np.ndarray, f: np.ndarray) -> None:
    """A binary STL, as Inventor writes one."""
    out = [b"\0" * 80, struct.pack("<I", len(f))]
    for a, b, c in f:
        n = np.cross(v[b] - v[a], v[c] - v[a])
        out.append(struct.pack("<12fH", *n, *v[a], *v[b], *v[c], 0))
    path.write_bytes(b"".join(out))


def write_obj(path: Path, v: np.ndarray, f: np.ndarray) -> None:
    lines = [f"v {x:.9g} {y:.9g} {z:.9g}" for x, y, z in v] + [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in f]
    path.write_text("\n".join(lines) + "\n")


def concave_l(path: Path) -> None:
    """An L-shaped block (two boxes sharing a face): concave, its hull a third bigger."""
    # an L in x-z, 0.1 thick in y: the union of [0,0.3]x[0,0.1] and [0,0.1]x[0,0.3]
    pts2 = [(0, 0), (0.3, 0), (0.3, 0.1), (0.1, 0.1), (0.1, 0.3), (0, 0.3)]
    n = len(pts2)
    v = [(x, y, z) for y in (0.0, 0.1) for x, z in pts2]
    f = []
    # caps: a fan from the L's inner corner (index 3), which sees every edge
    for i in range(n):
        a, b = i, (i + 1) % n
        if 3 in (a, b):
            continue
        f.append((3, b, a))
        f.append((3 + n, a + n, b + n))
    for i in range(n):
        a, b = i, (i + 1) % n
        f += [(a, b, b + n), (a, b + n, a + n)]
    write_obj(path, np.array(v, dtype=float), np.array(f))


# --------------------------------------------------------------------------------------------------- meshes

def test_mesh_units_scale(tmp_path):
    v, f = cube(0.0, 100.0)  # a 100 mm cube
    write_stl(tmp_path / "block.stl", v, f)
    raw = tomllib.loads(HEAD + """
[[cad]]
name = "block"
mesh = "block.stl"
units = "mm"
center = [0.3, 0.0, 0.0]
material = "aluminum"
""")
    built = build_bench(parse_bench(raw, tmp_path))
    m, lay = built.model, built.layout
    (g,) = [g for g, s in lay.shape_of_geom.items() if s == "block"]
    assert m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    R = d.geom_xmat[g].reshape(3, 3)
    half = np.abs(R) @ m.geom_aabb[g, 3:]
    center = d.geom_xpos[g] + R @ m.geom_aabb[g, :3]
    assert np.allclose(2 * half, 0.1, rtol=1e-6)  # 100 mm is 0.1 m
    assert np.allclose(center, [0.35, 0.05, 0.05], atol=1e-9)  # the file's origin at center
    assert m.geom_contype[g] == 1 and m.geom_conaffinity[g] == 0  # a shape: touches pieces only


def test_mesh_inches_and_rotation(tmp_path):
    v, f = cube(0.0, 1.0)  # a 1 in. cube
    write_obj(tmp_path / "block.obj", v, f)
    raw = tomllib.loads(HEAD + """
[[cad]]
name = "block"
mesh = "block.obj"
units = "in"
rot_deg = [0.0, 0.0, 90.0]
""")
    built = build_bench(parse_bench(raw, tmp_path))
    m, lay = built.model, built.layout
    (g,) = lay.shape_of_geom
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    R = d.geom_xmat[g].reshape(3, 3)
    center = d.geom_xpos[g] + R @ m.geom_aabb[g, :3]
    assert np.allclose(center, [-IN / 2, IN / 2, IN / 2], atol=1e-9)  # turned 90 deg about z


def test_convex_mesh_needs_no_extra(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "coacd", None)  # coacd not installed
    v, f = cube(0.0, 50.0)
    write_stl(tmp_path / "block.stl", v, f)
    assert cad.convex_pieces(tmp_path / "block.stl", "mm") == [tmp_path / "block.stl"]
    assert not (tmp_path / "block.stl.convex").exists()  # nothing split, nothing cached
    raw = tomllib.loads(HEAD + '[[cad]]\nname = "block"\nmesh = "block.stl"\nunits = "mm"\n')
    built = build_bench(parse_bench(raw, tmp_path))
    assert list(built.layout.shape_of_geom.values()) == ["block"]


def test_concave_without_coacd_errors(tmp_path, monkeypatch):
    """Review Focus 5: a concave mesh without coacd stops, saying the mesh is concave and how to get coacd; it never
    runs with the hole filled in by a hull."""
    monkeypatch.setitem(sys.modules, "coacd", None)
    monkeypatch.setattr(cad, "TEMP_CACHE", tmp_path / "temp-cache")  # no split cached from another run
    concave_l(tmp_path / "ell.obj")
    with pytest.raises(BenchError, match=r"ell\.obj.*concave.*pip install scrimmage\[bench\]"):
        cad.convex_pieces(tmp_path / "ell.obj", "m")
    raw = tomllib.loads(HEAD + '[[cad]]\nname = "ell"\nmesh = "ell.obj"\nunits = "m"\n')
    with pytest.raises(BenchError, match=r"\[\[cad\]\] 'ell'.*concave.*pip install scrimmage\[bench\]"):
        build_bench(parse_bench(raw, tmp_path))


def test_missing_mesh_is_an_error(tmp_path):
    raw = tomllib.loads(HEAD + '[[cad]]\nname = "ghost"\nmesh = "ghost.stl"\nunits = "mm"\n')
    with pytest.raises(BenchError, match=r"\[\[cad\]\] 'ghost'.*ghost\.stl"):
        build_bench(parse_bench(raw, tmp_path))


def hood_obj(path: Path, bench_raw: dict, step_deg: float = 1.0) -> None:
    """The hooded example's flare and hood as one concave OBJ in millimeters: each curve's inside surface at its
    radius (linear in angle, as the bench's curves are), its thickness outside, sampled every step_deg."""
    curves = bench_raw["curve"]
    t, w = 0.125 * IN, curves[0]["width"]
    pts = []
    for k, c in enumerate(curves):
        r0, r1 = c.get("radius_start", c.get("radius")), c.get("radius_end", c.get("radius"))
        a0, a1 = c["start_deg"], c["end_deg"]
        n = max(1, math.ceil(abs(a1 - a0) / step_deg))
        last = k == len(curves) - 1
        pts += [(a0 + (a1 - a0) * i / n, r0 + (r1 - r0) * i / n) for i in range(n + (1 if last else 0))]
    v = []
    for a, r in pts:
        th = math.radians(a)
        for rr in (r, r + t):
            for y in (-w / 2, w / 2):
                v.append((rr * math.cos(th) * 1000, y * 1000, rr * math.sin(th) * 1000))
    n = len(pts)

    def q(a, b, c, d, flip):
        return [(a, c, b), (a, d, c)] if flip else [(a, b, c), (a, c, d)]

    idx = lambda i, o, s: 4 * i + 2 * o + s  # noqa: E731 (point, outside, side)
    f = []
    for i in range(n - 1):
        for o in (0, 1):
            f += q(idx(i, o, 0), idx(i + 1, o, 0), idx(i + 1, o, 1), idx(i, o, 1), not o)
        for s in (0, 1):
            f += q(idx(i, 0, s), idx(i + 1, 0, s), idx(i + 1, 1, s), idx(i, 1, s), bool(s))
    for i, flip in ((0, False), (n - 1, True)):
        f += q(idx(i, 0, 0), idx(i, 1, 0), idx(i, 1, 1), idx(i, 0, 1), flip)
    write_obj(path, np.array(v), np.array(f))


@pytest.mark.skipif(not HAVE_COACD, reason="coacd is not installed (pip install scrimmage[bench])")
def test_concave_split_and_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(cad, "TEMP_CACHE", tmp_path / "temp-cache")
    raw = tomllib.loads(HOODED.read_text())
    hood_obj(tmp_path / "hood.obj", raw)
    t0 = time.perf_counter()
    pieces = cad.convex_pieces(tmp_path / "hood.obj", "mm")
    split = time.perf_counter() - t0
    print(f"\ncoacd split the hood into {len(pieces)} pieces in {split:.2f} s")
    assert len(pieces) > 4
    assert all(p.parent.parent == tmp_path / "hood.obj.convex" for p in pieces)
    # a rerun reads the cache: same files, no split
    def no_split(v, f):
        raise AssertionError("split again instead of reading the cache")

    monkeypatch.setattr(cad, "_split", no_split)
    assert cad.convex_pieces(tmp_path / "hood.obj", "mm") == pieces
    monkeypatch.undo()
    monkeypatch.setattr(cad, "TEMP_CACHE", tmp_path / "temp-cache")
    # keyed by units too: the same file in meters is another split
    assert cad.cache_key(tmp_path / "hood.obj", "m") != pieces[0].parent.name

    # the hood as a mesh in place of the curves gives the curves' exit speed
    one = {"inlet.feed.count": 1}
    ref = run_bench(parse_bench(with_overrides(raw, one), HOODED.parent))
    raw_cad = with_overrides(raw, one)
    del raw_cad["curve"]
    raw_cad["cad"] = [{"name": "hood", "mesh": "hood.obj", "units": "mm", "material": "polycarbonate"}]
    bench = parse_bench(raw_cad, tmp_path)
    built = build_bench(bench)
    check_clear(built)
    assert sorted(set(built.layout.shape_of_geom.values())) == ["hood", "wheel"]
    got = run_bench(bench)
    (a,), (b,) = ref.pieces, got.pieces
    assert a.status == b.status == "out"
    print(f"hood as curves {a.speed:.3f} m/s {a.elevation_deg:.1f} deg, as a split OBJ {b.speed:.3f} m/s "
          f"{b.elevation_deg:.1f} deg ({100 * (b.speed / a.speed - 1):+.2f}%)")
    assert b.speed == pytest.approx(a.speed, rel=0.05)


def test_read_only_folder_uses_a_temp_cache(tmp_path, monkeypatch):
    """When the mesh's folder can't be written, the split goes to a cache under the temp folder, with a warning."""
    concave_l(tmp_path / "ell.obj")
    monkeypatch.setattr(cad, "_split", lambda v, f: [cube(0.0, 0.1), cube(0.1, 0.2)])  # no coacd needed
    monkeypatch.setattr(cad, "TEMP_CACHE", tmp_path / "temp-cache")
    real_mkdir = Path.mkdir

    def no_write(self, *a, **k):
        if str(self).startswith(str(tmp_path / "ell.obj.convex")):
            raise PermissionError(13, "Permission denied", str(self))
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(Path, "mkdir", no_write)
    with pytest.warns(UserWarning, match=r"can't write.*ell\.obj\.convex"):
        pieces = cad.convex_pieces(tmp_path / "ell.obj", "m")
    assert len(pieces) == 2 and all((tmp_path / "temp-cache") in p.parents for p in pieces)


# ----------------------------------------------------------------------------------------------- part folders

PART = """
[part]
name = "roller_box"
format = 1

[[body]]
name = "frame"
mass = 1.0
geom = [{ box = [0.2, 0.2, 0.01], pos = [0.0, 0.0, -0.1] },
        { box = [0.05, 0.05, 0.05], pos = [0.0, 0.0, 0.3], collide = false }]

[[body]]
name = "roller"
parent = "frame"
mass = 0.3
pos = [0.0, 0.0, 0.05]
joint = { name = "spin", hinge = "y" }
geom = [{ cylinder = [0.05, 0.1], axis = "y", friction = 1.2, condim = 4 }]

[[body]]
name = "flap"
parent = "frame"
mass = 0.1
pos = [0.08, 0.0, 0.0]
joint = { name = "flap_pivot", hinge = "y" }
geom = [{ box = [0.01, 0.1, 0.05] }]

[[motor]]
slot = "roller_motor"
joint = "spin"
motor = "NEO"
"""

SHAFT = """
[shaft.intake]
motor = "NEO"
target_rps = 20
kP = 0.1
wheel_inertia = 0.0004
"""


def part_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "parts" / "roller_box"
    folder.mkdir(parents=True)
    (folder / "part.toml").write_text(PART)
    return folder


def test_part_folder_slot_becomes_shaft(tmp_path):
    part_folder(tmp_path)
    raw = tomllib.loads(HEAD + SHAFT + """
[[cad]]
name = "box"
part = "parts/roller_box"
center = [1.0, 0.0, 0.5]
material = "aluminum"
tread = "45A"
slots = { roller_motor = "intake" }
""")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        built = build_bench(parse_bench(raw, tmp_path))
    assert any("flap_pivot" in str(w.message) and "welded" in str(w.message) for w in caught)
    m, lay = built.model, built.layout
    # the roller turns on the intake shaft, about the part's hinge axis, at the roller's place
    j = m.joint("shaft:intake").id
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    assert np.allclose(d.xanchor[j], [1.0, 0.0, 0.55])
    assert np.allclose(d.xaxis[j], [0.0, 1.0, 0.0])
    wheel = lay.wheel_geoms["box.roller"]
    assert m.geom_bodyid[wheel] == m.jnt_bodyid[j]
    assert m.geom_type[wheel] == mujoco.mjtGeom.mjGEOM_CYLINDER
    assert np.allclose(m.geom_size[wheel][:2], [0.05, 0.05])
    assert np.allclose(d.geom_xpos[wheel], [1.0, 0.0, 0.55])
    assert lay.cad_wheels["box.roller"].shaft == "intake"
    assert lay.cad_wheels["box.roller"].tread == "45A"
    assert lay.shaft_dof["intake"] == m.jnt_dofadr[j]
    # the frame and the flap are welded to the bench (the world); the look-only geom isn't on the bench
    shapes = sorted(set(lay.shape_of_geom.values()))
    assert shapes == ["box.flap", "box.frame", "box.roller"]
    for g, s in lay.shape_of_geom.items():
        if s != "box.roller":
            assert m.body_weldid[m.geom_bodyid[g]] == 0, s
    assert m.njnt == 1 + len(lay.piece_qpos)  # the shaft's hinge and the pieces' free joints only
    assert lay.cad_material["box.frame"] == "aluminum"


def test_part_folder_runs_a_ball_through_the_roller(tmp_path):
    """The slotted roller is driven by its shaft: a ball dropped on it is thrown along the roller's spin."""
    part_folder(tmp_path)
    raw = tomllib.loads(HEAD.replace("""[piece]
name = "car"
kind = "rigid"
shape = "box"
size = [0.10, 0.06, 0.04]
mass = 0.2
""", '[piece]\npreset = "2026-fuel"\n') + SHAFT + """
[[cad]]
name = "box"
part = "parts/roller_box"
slots = { roller_motor = "intake" }

[[inlet]]
name = "drop"
center = [0.0, 0.0, 0.18]
size = [0.001, 0.001, 0.001]
count = 1

[[outlet]]
name = "away"
center = [0.0, 0.0, -0.6]
size = [2.0, 1.0, 0.2]

[run]
duration = 1.5
""")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = run_bench(parse_bench(raw, tmp_path))
    (p,) = result.pieces
    # the roller spins positively about +y: its top moves toward +x, so the ball leaves going +x
    assert p.path[-1][1] > 0.05, p.path[-1]


def test_part_folder_slot_errors(tmp_path):
    part_folder(tmp_path)
    base = HEAD + SHAFT + '[[cad]]\nname = "box"\npart = "parts/roller_box"\n'
    with pytest.raises(BenchError, match=r"\[\[cad\]\] 'box'.*'nope'.*roller_motor"):
        build_bench(parse_bench(tomllib.loads(base + 'slots = { nope = "intake" }\n'), tmp_path))
    with pytest.raises(BenchError, match=r"\[\[cad\]\] 'box'.*part folder"):
        build_bench(parse_bench(tomllib.loads(base.replace("roller_box", "ghost")), tmp_path))


def test_mesh_has_no_tread(tmp_path):
    v, f = cube(0.0, 1.0)
    write_obj(tmp_path / "block.obj", v, f)
    raw = tomllib.loads(HEAD + '[[cad]]\nname = "b"\nmesh = "block.obj"\nunits = "m"\ntread = "35A"\n')
    with pytest.raises(BenchError, match="tread"):
        parse_bench(raw, tmp_path)


def test_part_folder_collide_mesh(tmp_path):
    """A body marked collide collides through its mesh (in meters, in the body's frame); a look-only mesh is left
    off."""
    folder = tmp_path / "parts" / "guide"
    (folder / "meshes").mkdir(parents=True)
    v, f = cube(0.0, 0.1)
    write_stl(folder / "meshes" / "guide.stl", v, f)
    write_stl(folder / "meshes" / "look.stl", v, f)
    (folder / "part.toml").write_text("""
[part]
name = "guide"
format = 1

[[body]]
name = "guide"
mass = 1.0
geom = [{ mesh = "meshes/guide.stl", pos = [0.0, 0.0, 0.2] }, { mesh = "meshes/look.stl", collide = false }]
""")
    raw = tomllib.loads(HEAD + '[[cad]]\nname = "g"\npart = "parts/guide"\ncenter = [1.0, 0.0, 0.0]\n'
                        'rot_deg = [0.0, 0.0, 90.0]\n')
    built = build_bench(parse_bench(raw, tmp_path))
    m, lay = built.model, built.layout
    (g,) = lay.shape_of_geom
    assert lay.shape_of_geom[g] == "g.guide" and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH
    d = mujoco.MjData(m)
    mujoco.mj_kinematics(m, d)
    R = d.geom_xmat[g].reshape(3, 3)
    center = d.geom_xpos[g] + R @ m.geom_aabb[g, :3]
    assert np.allclose(center, [0.95, 0.05, 0.25], atol=1e-9)  # turned 90 deg about z, then moved to center


def ell_mesh() -> tuple[np.ndarray, np.ndarray]:
    pts2 = [(0, 0), (0.3, 0), (0.3, 0.1), (0.1, 0.1), (0.1, 0.3), (0, 0.3)]
    n = len(pts2)
    v = np.array([(x, y, z) for y in (0.0, 0.1) for x, z in pts2], dtype=float)
    f = []
    for i in range(n):
        a, b = i, (i + 1) % n
        if 3 in (a, b):
            continue
        f += [(3, b, a), (3 + n, a + n, b + n)]
    for i in range(n):
        a, b = i, (i + 1) % n
        f += [(a, b, b + n), (a, b + n, a + n)]
    return v, np.array(f)


def test_open_mesh_is_not_a_solid(tmp_path, monkeypatch):
    """An open concave mesh far from the origin (assembly coordinates) has no volume to compare with its hull: it is
    an error, never run as its hull."""
    monkeypatch.setattr(cad, "TEMP_CACHE", tmp_path / "temp-cache")
    v, f = ell_mesh()
    f = np.array([t for t in f if not all(k >= 6 for k in t)])  # drop one cap: open
    write_obj(tmp_path / "open.obj", v + [0.0, 5.0, 0.0], f)
    with pytest.raises(BenchError, match=r"open\.obj isn't a closed solid \(\d+ open edges\).*closed solid"):
        cad.convex_pieces(tmp_path / "open.obj", "m")
    raw = tomllib.loads(HEAD + '[[cad]]\nname = "open"\nmesh = "open.obj"\nunits = "m"\n')
    with pytest.raises(BenchError, match=r"\[\[cad\]\] 'open'.*isn't a closed solid"):
        build_bench(parse_bench(raw, tmp_path))


def test_inconsistently_wound_mesh_is_not_a_solid(tmp_path):
    v, f = ell_mesh()
    f = f.copy()
    f[::2] = f[::2, ::-1]  # every other face turned inside out
    write_obj(tmp_path / "mixed.obj", v, f)
    with pytest.raises(BenchError, match=r"isn't a closed solid \(\d+ edges inconsistently wound\)") as e:
        cad.convex_pieces(tmp_path / "mixed.obj", "m")
    assert "concave" not in str(e.value)


def test_stl_corners_are_welded(tmp_path):
    """STL repeats each corner per face (and CAD rounds them): a closed STL is closed once they are merged."""
    v, f = cube(0.0, 100.0)
    write_stl(tmp_path / "block.stl", v + 1e-7 * np.arange(len(v))[:, None], f)
    assert cad.convex_pieces(tmp_path / "block.stl", "mm") == [tmp_path / "block.stl"]
