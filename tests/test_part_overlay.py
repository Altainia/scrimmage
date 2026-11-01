"""The plugin overlay (scrimmage-edits.toml): edits that survive a re-export of the part folder."""

import importlib
import shutil
import sys
import tomllib
import warnings
from pathlib import Path

import numpy as np
import pytest

from scrimmage.bench.cad import write_obj
from scrimmage.parts.model import PartError, load_part, part_from_dict, part_to_dict

ADDON = Path(__file__).resolve().parent.parent / "freecad" / "Scrimmage"

PART = """
[part]
name = "arm"
format = 1

[[body]]
name = "base"
mass = 2.0
geom = [{ box = [0.2, 0.2, 0.1] }]

[[body]]
name = "arm"
parent = "base"
mass = 1.0
com = [0.1, 0, 0]
inertia = [0.01, 0.02, 0.03]
geom = [{ mesh = "meshes/arm.obj", collide = false }]
joint = { name = "pivot", hinge = "y", range = [-90, 90] }

[[body]]
name = "wrist"
parent = "arm"
mass = 0.5
geom = [{ box = [0.05, 0.05, 0.05] }]
"""


def cuboid(lx, ly, lz):
    """A closed box mesh from (0,0,0) to (lx,ly,lz): (vertices, triangles)."""
    v = np.array([[x, y, z] for x in (0, lx) for y in (0, ly) for z in (0, lz)], dtype=float)
    f = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                  [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]])
    return v, f


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "arm"
    (d / "meshes").mkdir(parents=True)
    (d / "part.toml").write_text(PART)
    write_obj(d / "meshes" / "arm.obj", *cuboid(0.4, 0.1, 0.05))
    return d


def edit(folder, text):
    (folder / "scrimmage-edits.toml").write_text(text)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        part = load_part(folder)
    return part, [str(x.message) for x in w]


def body(part, name):
    return next(b for b in part.bodies if b.name == name)


def test_no_overlay_is_unchanged(folder):
    raw = tomllib.loads((folder / "part.toml").read_text())
    assert part_to_dict(load_part(folder)) == part_to_dict(part_from_dict(raw, "x", folder))


def test_overlay_changes_mass_and_joint_range(folder):
    part, w = edit(folder, "[body.arm]\nmass = 3.0\n[body.arm.joint]\nrange = [-10, 45]\ndamping = 0.5\n"
                           "frictionloss = 0.1\nspring = { stiffness = 2.0, rest = 5.0 }\n")
    a = body(part, "arm")
    assert w == [] and a.mass == 3.0 and a.joint.range == (-10, 45) and a.joint.damping == 0.5
    assert a.joint.frictionloss == 0.1 and a.joint.spring == (2.0, 5.0)
    assert a.com == (0.1, 0, 0) and a.inertia == pytest.approx((0.03, 0.06, 0.09))     # scaled by 3
    assert body(part, "base").mass == 2.0


def test_mass_without_inertia_just_sets_mass(folder):
    part, _ = edit(folder, "[body.base]\nmass = 5.0\n")
    assert body(part, "base").mass == 5.0 and body(part, "base").inertia is None


def test_missing_body_warns_not_errors(folder):
    part, w = edit(folder, "[body.hook_B]\nmass = 1.0\n[body.arm]\nmass = 4.0\n")
    assert w == ["scrimmage-edits.toml: body hook_B is no longer in the part; its edits were dropped"]
    assert body(part, "arm").mass == 4.0


def test_total_mass_scales_the_bodies_not_set(folder):
    part, _ = edit(folder, "total_mass = 10.0\n[body.base]\nmass = 4.0\n")
    assert body(part, "base").mass == 4.0
    assert body(part, "arm").mass == pytest.approx(6.0 * 1.0 / 1.5)
    assert body(part, "wrist").mass == pytest.approx(6.0 * 0.5 / 1.5)
    assert body(part, "arm").inertia == pytest.approx((0.04, 0.08, 0.12))


def test_added_slots_and_mounts(folder):
    part, _ = edit(folder, '[[motor]]\nslot = "pivot"\njoint = "pivot"\nmotor = "KrakenX60"\ngearing = 50\n'
                           '[[encoder]]\nslot = "enc"\njoint = "pivot"\n[[gyro]]\nslot = "gyro"\nbody = "base"\n'
                           '[[mount]]\nname = "tip"\nbody = "wrist"\npos = [0.1, 0, 0]\n')
    assert [m.slot for m in part.motors] == ["pivot"] and part.motors[0].gearing == 50
    assert [e.slot for e in part.encoders] == ["enc"] and [g.slot for g in part.gyros] == ["gyro"]
    assert part.mounts[0].name == "tip" and part.mounts[0].pos == (0.1, 0, 0)


def test_collision_none_and_simple(folder):
    part, _ = edit(folder, '[body.base]\ncollision = "none"\n[body.arm]\ncollision = "simple"\n')
    assert not any(g.collide for g in body(part, "base").geoms)
    colliding = [g for g in body(part, "arm").geoms if g.collide]
    assert len(colliding) == 1 and colliding[0].shape == "box"
    assert sorted(colliding[0].size) == pytest.approx([0.05, 0.1, 0.4])
    assert colliding[0].pos == pytest.approx((0.2, 0.05, 0.025))
    assert any(g.shape == "mesh" and not g.collide for g in body(part, "arm").geoms)    # the looks stay


def test_collision_wheel_about_the_hinge(folder):
    part, _ = edit(folder, '[body.arm]\ncollision = "wheel"\n')
    (g,) = [g for g in body(part, "arm").geoms if g.collide]
    assert g.shape == "cylinder" and g.friction == 1.2 and g.condim == 4
    assert g.axis == "y" or g.rot != (0, 0, 0)
    assert g.size[0] == pytest.approx(np.hypot(0.4, 0.05)) and g.size[1] == pytest.approx(0.1)


def test_collision_wheel_needs_a_hinge(folder):
    text = (folder / "part.toml").read_text().replace("geom = [{ box = [0.05, 0.05, 0.05] }]",
                                                      'geom = [{ mesh = "meshes/arm.obj", collide = false }]')
    (folder / "part.toml").write_text(text)
    part, w = edit(folder, '[body.wrist]\ncollision = "wheel"\n')
    assert any("wrist" in x and "hinge" in x for x in w)
    assert [g.shape for g in body(part, "wrist").geoms if g.collide] == ["box"]


def test_collision_without_a_mesh_warns_and_stays(folder):
    part, w = edit(folder, '[body.base]\ncollision = "simple"\n')
    assert any("base" in x and "no mesh" in x for x in w)
    assert body(part, "base").geoms[0].size == (0.2, 0.2, 0.1)


def test_collision_shaped_uses_convex_pieces(folder):
    part, w = edit(folder, '[body.arm]\ncollision = "shaped"\n')
    colliding = [g for g in body(part, "arm").geoms if g.collide]
    assert w == [] and colliding and all(g.shape == "mesh" and Path(g.size).is_file() for g in colliding)


def test_bad_collision_word_is_a_part_error(folder):
    (folder / "scrimmage-edits.toml").write_text('[body.arm]\ncollision = "round"\n')
    with pytest.raises(PartError, match="collision"):
        load_part(folder)


def test_unknown_key_is_a_part_error(folder):
    (folder / "scrimmage-edits.toml").write_text('[body.arm]\nmas = 1\n')
    with pytest.raises(PartError, match="mas"):
        load_part(folder)


def addon_module(name):
    sys.path.insert(0, str(ADDON))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(ADDON))


def test_install_keeps_the_overlay(tmp_path):
    export = addon_module("scrimmage_part.export")
    out, tmp = tmp_path / "x", tmp_path / ".x.tmp"
    out.mkdir()
    tmp.mkdir()
    (out / "part.toml").write_text("old")
    (out / "scrimmage-edits.toml").write_text("[body.a]\nmass = 1\n")
    (tmp / "part.toml").write_text("new")
    export._install(str(tmp), str(out))
    assert (out / "part.toml").read_text() == "new"
    assert (out / "scrimmage-edits.toml").read_text() == "[body.a]\nmass = 1\n"
    assert [p.name for p in tmp_path.iterdir()] == ["x"]


def test_install_without_an_overlay_adds_none(tmp_path):
    export = addon_module("scrimmage_part.export")
    out, tmp = tmp_path / "x", tmp_path / ".x.tmp"
    out.mkdir()
    tmp.mkdir()
    (out / "part.toml").write_text("old")
    (tmp / "part.toml").write_text("new")
    export._install(str(tmp), str(out))
    assert sorted(p.name for p in out.iterdir()) == ["part.toml"]
