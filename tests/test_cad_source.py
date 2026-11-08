"""Reading CAD sources: STEP assemblies (names, nesting, positions) and STL/OBJ folders (units)."""

import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

from scrimmage.cad import CadError, missing_extra
from scrimmage.cad.mass import is_closed, mesh_inertial
from scrimmage.cad.source import UNITS, guess_units, load_source

DATA = Path(__file__).parent / "data" / "cad"


def piece(s, key):
    return next(p for p in s.pieces if p.key == key)


def test_step_names_nesting_positions():
    s = load_source(DATA / "climber.step")
    keys = [p.key for p in s.pieces]
    assert "Climber/base_plate" in keys and "Climber/hook_assy/hook" in keys and "Climber/hook_assy#2/hook" in keys
    arm = piece(s, "Climber/arm_A")
    assert arm.group == ("Climber",)
    assert piece(s, "Climber/hook_assy#2/hook").group == ("Climber", "Climber/hook_assy#2")
    assert arm.v.min(axis=0) == pytest.approx([0.05, 0.09, 0.01], abs=1e-6)     # meters, in CAD coords
    assert mesh_inertial(arm.v, arm.f, 1000).mass == pytest.approx(0.02 * 0.02 * 0.8 * 1000, rel=1e-6)
    assert is_closed(arm.f)
    assert s.kind == "step"


def test_repeated_bolts_get_numbered_keys():
    s = load_source(DATA / "climber.step")
    keys = [p.key for p in s.pieces]
    assert "Climber/bolt" in keys
    for i in range(2, 7):
        assert f"Climber/bolt#{i}" in keys
    assert "Climber/bolt#7" not in keys


def test_stl_folder_units_guessed_mm():
    s = load_source(DATA / "stl")
    assert s.units == "mm" and s.kind == "mesh"
    base = piece(s, "base_plate")
    assert np.ptp(base.v, axis=0).max() == pytest.approx(0.3, abs=1e-6)


def test_units_override():
    mm = piece(load_source(DATA / "stl" / "base_plate.stl", units="mm"), "base_plate")
    inch = piece(load_source(DATA / "stl" / "base_plate.stl", units="in"), "base_plate")
    assert np.ptp(inch.v, axis=0).max() == pytest.approx(300 * 0.0254, rel=1e-9)
    assert np.ptp(mm.v, axis=0).max() == pytest.approx(0.3, rel=1e-9)


def test_guess_units_thresholds():
    assert guess_units(0.8) == "m" and guess_units(30) == "in" and guess_units(800) == "mm"
    assert UNITS == {"m": 1.0, "mm": 0.001, "in": 0.0254}


def test_missing_extra_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "cascadio", None)
    with pytest.raises(CadError) as e:
        load_source(DATA / "climber.step")
    assert str(e.value) == missing_extra()


def test_unreadable_file_names_it(tmp_path):
    bad = tmp_path / "garbage.step"
    bad.write_text("this is not a STEP file")
    with pytest.raises(CadError, match="garbage.step"):
        load_source(bad)


def test_path_with_spaces_and_unicode(tmp_path):
    d = tmp_path / "my parts"
    d.mkdir()
    dst = d / "Klättrare.step"
    shutil.copy(DATA / "climber.step", dst)
    assert len(load_source(dst).pieces) > 5


def test_v2_and_yup_fixtures_load():
    v2 = load_source(DATA / "climber_v2.step")
    keys = [p.key for p in v2.pieces]
    assert "Climber/hook_assy_right/hook" in keys and "Climber/bolt#5" in keys and "Climber/bolt#6" not in keys
    assert np.ptp(piece(v2, "Climber/arm_B").v, axis=0)[2] == pytest.approx(0.9, abs=1e-6)
    yup = piece(load_source(DATA / "climber_yup.step"), "Climber/arm_A")
    assert np.ptp(yup.v, axis=0)[1] == pytest.approx(0.8, abs=1e-6)


def test_inside_out_stl_same_mass():
    a = load_source(DATA / "stl" / "hook.stl").pieces[0]
    b = load_source(DATA / "stl" / "hook_inside_out.stl").pieces[0]
    assert mesh_inertial(a.v, a.f, 1000).mass == pytest.approx(mesh_inertial(b.v, b.f, 1000).mass, rel=1e-6)


# ---- a glTF mesh with several primitives (the official 2024 STAGE STEP, TE-24200: 81 meshes, 84 primitives)

def _glb(meshes, nodes, *, bad_index=False):
    """A GLB whose meshes are lists of (vertices (n, 3), faces (m, 3)) primitives, and whose nodes are
    (name, mesh index or None, translation, children) under one scene of root nodes 0.."""
    import json
    import struct
    blob, views, accessors, out_meshes = b"", [], [], []
    for prims in meshes:
        ps = []
        for v, f in prims:
            v = np.asarray(v, dtype=np.float32)
            f = np.asarray(f, dtype=np.uint32)
            if bad_index:
                f = f + 1000
            for arr, kind, comp, count in ((v, "VEC3", 5126, len(v)), (f.ravel(), "SCALAR", 5125, f.size)):
                views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": arr.nbytes})
                acc = {"bufferView": len(views) - 1, "componentType": comp, "count": count, "type": kind}
                if kind == "VEC3":
                    acc["min"], acc["max"] = v.min(axis=0).tolist(), v.max(axis=0).tolist()
                accessors.append(acc)
                blob += arr.tobytes()
            ps.append({"attributes": {"POSITION": len(accessors) - 2}, "indices": len(accessors) - 1, "mode": 4})
        out_meshes.append({"primitives": ps})
    gl_nodes = []
    for name, mesh, t, children in nodes:
        n = {"name": name, "translation": list(t)}
        if mesh is not None:
            n["mesh"] = mesh
        if children:
            n["children"] = list(children)
        gl_nodes.append(n)
    child = {c for _, _, _, cs in nodes for c in cs}
    doc = {"asset": {"version": "2.0"}, "scene": 0,
           "scenes": [{"nodes": [i for i in range(len(nodes)) if i not in child]}],
           "nodes": gl_nodes, "meshes": out_meshes, "accessors": accessors, "bufferViews": views,
           "buffers": [{"byteLength": len(blob)}]}
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    blob += b"\0" * (-len(blob) % 4)
    body = struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(blob), 0x004E4942) + blob
    return struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body


def _box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    v = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    f = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return v, f


def _fake_cascadio(monkeypatch, glb: bytes):
    import types
    fake = types.ModuleType("cascadio")

    def step_to_glb(src, dst, **_):
        Path(dst).write_bytes(glb)
        return 0
    fake.step_to_glb = step_to_glb
    monkeypatch.setitem(sys.modules, "cascadio", fake)


def test_a_mesh_with_two_primitives_loads_as_one_piece(tmp_path, monkeypatch):
    # mesh 0 has two primitives (two boxes, as OpenCASCADE writes a part with two colors); mesh 1 has one. trimesh
    # splits that into 3 geometries, which a count check took for a broken file
    glb = _glb([[_box([0, 0, 0], [0.1, 0.1, 0.1]), _box([0.2, 0, 0], [0.3, 0.1, 0.1])],
                [_box([0, 0, 0], [0.05, 0.05, 0.05])]],
               [("Stage", None, (0, 0, 0), [1, 2]), ("leg", 0, (1, 0, 0), []), ("chain", 1, (0, 2, 0), [])])
    _fake_cascadio(monkeypatch, glb)
    step = tmp_path / "stage.step"
    step.write_text("ISO-10303-21;")
    s = load_source(step)
    leg, chain = piece(s, "Stage/leg"), piece(s, "Stage/chain")
    assert len(leg.v) == 16 and len(leg.f) == 24           # both primitives, vertices merged per box
    assert leg.v.min(axis=0) == pytest.approx([1.0, 0, 0]) and leg.v.max(axis=0) == pytest.approx([1.3, 0.1, 0.1])
    assert mesh_inertial(leg.v, leg.f, 1000).mass == pytest.approx(2 * 0.001 * 1000, rel=1e-6)
    assert chain.v.min(axis=0) == pytest.approx([0, 2, 0]) and len(chain.f) == 12


def test_a_glb_with_bad_indices_is_still_a_broken_file(tmp_path, monkeypatch):
    glb = _glb([[_box([0, 0, 0], [0.1, 0.1, 0.1])]], [("leg", 0, (0, 0, 0), [])], bad_index=True)
    _fake_cascadio(monkeypatch, glb)
    step = tmp_path / "broken.step"
    step.write_text("ISO-10303-21;")
    with pytest.raises(CadError, match="broken.step.*re-export"):
        load_source(step)
