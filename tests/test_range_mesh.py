"""The `kind = "mesh"` field element (testrange/mesh.py): parse, cache, build and footprint."""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from cad_helpers import FIELD_STEP
from range_fixtures import range_cfg
from scrimmage.assembler.view import model_view
from scrimmage.protocol import HalState
from scrimmage.testrange import mesh as mesh_module
from scrimmage.testrange.elements import Mesh, footprint
from scrimmage.testrange.mesh import MAX_MESH_TRIANGLES
from scrimmage.testrange.spec import RangeError, load_range
from scrimmage.world import World

CAD = Path(__file__).parent / "data" / "cad"
HEAD = '''[range]
name = "test"
ball = "2026-fuel"

'''
CUBE_F = [(0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5), (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6), (0, 2, 6),
          (0, 6, 4), (1, 5, 7), (1, 7, 3)]


@pytest.fixture(autouse=True)
def cache(request, tmp_path, monkeypatch):
    """A cache of its own, for the selection and for the convex splits. (The real field's test keeps the real one: its
    first split of the STAGE's frame takes minutes.)"""
    from scrimmage.cad import fit
    if "field_stage" in request.node.name:
        return
    monkeypatch.setattr(mesh_module, "MESH_CACHE", tmp_path / "mesh-cache")
    monkeypatch.setattr(fit, "SHAPED_CACHE", tmp_path / "shaped")


def write_stl(path: Path, v, f) -> None:
    v = np.asarray(v, dtype=float)
    out = [b"\0" * 80, struct.pack("<I", len(f))]
    for a, b, c in f:
        n = np.cross(v[b] - v[a], v[c] - v[a])
        out.append(struct.pack("<12fH", *n, *v[a], *v[b], *v[c], 0))
    path.write_bytes(b"".join(out))


def cube(side: float):
    return np.array([(x, y, z) for x in (0, side) for y in (0, side) for z in (0, side)], dtype=float), CUBE_F


def element(body: str) -> str:
    return HEAD + '[[element]]\nname = "m"\nkind = "mesh"\n' + body


def load(tmp_path, body: str, **kw):
    (tmp_path / "r.toml").write_text(element(body))
    return load_range("r.toml", tmp_path, **kw)


def mesh_of(tmp_path, body: str) -> Mesh:
    return load(tmp_path, body).elements[0]


CLIMBER = f'file = "{(CAD / "climber.step").as_posix()}"\n'


def test_a_mesh_element_from_a_step(tmp_path):
    arms = mesh_of(tmp_path, CLIMBER + 'select = ["Climber/arm_*"]\n')
    assert isinstance(arms, Mesh) and arms.pieces == ("Climber/arm_A", "Climber/arm_B")
    assert arms.lo == pytest.approx([0.05, 0.09, 0.01], abs=1e-6) and arms.hi == pytest.approx([0.25, 0.11, 0.81],
                                                                                              abs=1e-6)
    moved = mesh_of(tmp_path, CLIMBER + 'select = ["Climber/arm_*"]\npos = [2, 3, 0]\n')
    assert moved.lo == pytest.approx(arms.lo + [2, 3, 0], abs=1e-6)
    assert moved.hi == pytest.approx(arms.hi + [2, 3, 0], abs=1e-6)
    turned = mesh_of(tmp_path, CLIMBER + 'select = ["Climber/arm_*"]\nrpy_deg = [0, 0, 90]\n')
    assert turned.hi[0] - turned.lo[0] == pytest.approx(arms.hi[1] - arms.lo[1], abs=1e-6)
    assert turned.hi[1] - turned.lo[1] == pytest.approx(arms.hi[0] - arms.lo[0], abs=1e-6)
    sx, sy = np.ptp(footprint(turned)[:, 0]), np.ptp(footprint(turned)[:, 1])
    assert (sx, sy) == pytest.approx((0.02, 0.2), abs=1e-6)


def test_select_left_out_takes_every_piece(tmp_path):
    every = mesh_of(tmp_path, CLIMBER)
    assert len(every.pieces) == 11 and every.select == ()
    by_name = mesh_of(tmp_path, CLIMBER + 'select = ["bolt"]\n')
    assert len(by_name.pieces) == 6


def test_a_select_that_matches_nothing_names_close_names(tmp_path):
    with pytest.raises(RangeError, match=r"element 'm'.*bolts.*close names: bolt"):
        load(tmp_path, CLIMBER + 'select = ["bolts"]\n')


def test_units_for_an_stl_and_a_steps_own(tmp_path, monkeypatch):
    folder = tmp_path / "stl"
    folder.mkdir()
    write_stl(folder / "block.stl", *cube(300.0))
    e = mesh_of(tmp_path, 'file = "stl"\nunits = "mm"\n')
    assert e.hi - e.lo == pytest.approx([0.3, 0.3, 0.3], abs=1e-6)
    import scrimmage.cad.source as source
    monkeypatch.setattr(source, "step_length_unit", lambda path: "cm")
    plain = mesh_of(tmp_path, CLIMBER + 'select = ["Climber/arm_*"]\n')
    mm = mesh_of(tmp_path, CLIMBER + 'select = ["Climber/arm_*"]\nunits = "mm"\n')
    assert mm.hi - mm.lo == pytest.approx((plain.hi - plain.lo) / 10.0, abs=1e-6)


def test_too_big_or_too_many_triangles_says_select_fewer(tmp_path, monkeypatch):
    with pytest.raises(RangeError, match=r"element 'm'.*larger than.*select fewer pieces"):
        load(tmp_path, CLIMBER + "scale = 100\n")
    monkeypatch.setattr(mesh_module, "MAX_MESH_TRIANGLES", 10)
    with pytest.raises(RangeError, match=r"element 'm'.*select fewer pieces.*Climber/hook_assy/hook \(100 triangles\)"):
        load(tmp_path, CLIMBER)
    assert MAX_MESH_TRIANGLES == 200_000


def test_mesh_keys_are_checked(tmp_path):
    with pytest.raises(RangeError, match="unknown key 'shape'"):
        load(tmp_path, CLIMBER + 'shape = "box"\n')
    with pytest.raises(RangeError, match="unknown key 'center'"):
        load(tmp_path, CLIMBER + "center = [1, 1]\n")
    with pytest.raises(RangeError, match="missing required key 'file'"):
        load(tmp_path, 'select = ["bolt"]\n')
    with pytest.raises(RangeError, match="select must be a list of text"):
        load(tmp_path, CLIMBER + 'select = "bolt"\n')
    with pytest.raises(RangeError, match="ground must be true or false"):
        load(tmp_path, CLIMBER + 'ground = 1\n')


def test_the_file_is_relative_to_the_range_file(tmp_path):
    (tmp_path / "cad").mkdir()
    write_stl(tmp_path / "cad" / "block.stl", *cube(0.5))
    assert mesh_of(tmp_path, 'file = "cad/block.stl"\n').hi - mesh_of(tmp_path, 'file = "cad/block.stl"\n').lo == \
        pytest.approx([0.5] * 3, abs=1e-6)
    with pytest.raises(RangeError, match="nothing.stl not found"):
        load(tmp_path, 'file = "cad/nothing.stl"\n')


def test_moving_the_element_uses_the_cache(tmp_path, monkeypatch):
    first = mesh_of(tmp_path, CLIMBER + 'select = ["Climber/arm_*"]\n')
    import scrimmage.cad.source as source

    def boom(*a, **k):
        raise AssertionError("read the CAD file again")
    monkeypatch.setattr(source, "load_source", boom)
    moved = mesh_of(tmp_path, CLIMBER + 'select = ["Climber/arm_*"]\npos = [1, 1, 0]\nrpy_deg = [0, 0, 30]\n')
    assert moved.pieces == first.pieces and len(moved.parts) == len(first.parts)


def test_an_edited_file_misses_the_cache(tmp_path, monkeypatch):
    copy = tmp_path / "c.step"
    copy.write_bytes((CAD / "climber.step").read_bytes())
    body = f'file = "{copy.as_posix()}"\nselect = ["Climber/arm_*"]\n'
    mesh_of(tmp_path, body)
    text = copy.read_bytes()
    copy.write_bytes(text.replace(b"\n", b"\n/* edited */\n", 1))     # a comment line: the same shapes
    reads = []
    import scrimmage.cad.source as source
    real = source.load_source
    monkeypatch.setattr(source, "load_source", lambda *a, **k: (reads.append(1), real(*a, **k))[1])
    mesh_of(tmp_path, body)
    assert reads


def test_no_mesh_element_reads_no_cad():
    code = ("import sys\nfrom pathlib import Path\nfrom scrimmage.testrange.spec import load_range\n"
            "for n in ('2024', '2026'):\n    load_range(n, '.')\n"
            "assert 'scrimmage.cad.source' not in sys.modules\nassert 'cascadio' not in sys.modules\n")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=Path(__file__).parent.parent, env={**__import__("os").environ,
                                                                 "PYTHONPATH": str(Path(__file__).parent.parent)})
    assert done.returncode == 0, done.stderr


def test_an_open_piece_is_left_out_with_a_warning(tmp_path):
    folder = tmp_path / "pieces"
    folder.mkdir()
    write_stl(folder / "block.stl", *cube(0.5))
    write_stl(folder / "sheet.stl", [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [(0, 1, 2), (0, 2, 3)])
    rng = load(tmp_path, 'file = "pieces"\nunits = "m"\n')
    e = rng.elements[0]
    assert e.pieces == ("block", "sheet") and e.hi - e.lo == pytest.approx([0.5] * 3, abs=1e-6)
    ours = [w for w in rng.warnings if "sheet" in w]
    assert len(ours) == 1 and "can't be made into a solid" in ours[0] and "block" not in ours[0]
    World(range_cfg(range={"file": str(tmp_path / "r.toml"), "floor_balls": False}), None)


def block_range(tmp_path, extra: str = "", start: str = "") -> Path:
    write_stl(tmp_path / "block.stl", *cube(0.5))
    path = tmp_path / "field.toml"
    path.write_text(element(f'file = "block.stl"\nunits = "m"\npos = [3, 3, 0]\n{extra}') + start
                    + "\n[[floor_balls]]\nat = [[1.0, 1.0]]\n")
    return path


def test_the_mesh_element_builds_and_a_ball_lands_on_it(tmp_path):
    w = World(range_cfg(range={"file": str(block_range(tmp_path)), "floor_balls": True}), None)
    m = w.model
    assert m.geom("element.m.0").type[0] == 7        # mjGEOM_MESH
    radius = w.parts.range.ball.diameter / 2.0
    a = m.jnt_qposadr[m.body_jntadr[m.body("ball.0").id]]
    w.data.qpos[a:a + 3] = [3.25, 3.25, 1.0]
    w.data.qvel[:] = 0.0
    for _ in range(int(2.0 / m.opt.timestep)):
        w.step(HalState())
    assert w.data.qpos[a + 2] == pytest.approx(0.5 + radius, abs=0.01)


def test_ground_picks_the_geom_group(tmp_path):
    from scrimmage.testrange.build_elements import ELEMENT_GROUP, NON_GROUND_GROUP
    for extra, group in (("", NON_GROUND_GROUP), ("ground = true\n", ELEMENT_GROUP)):
        w = World(range_cfg(range={"file": str(block_range(tmp_path, extra)), "floor_balls": False}), None)
        assert int(w.model.geom("element.m.0").group[0]) == group


def test_its_footprint_blocks_a_start(tmp_path):
    """The whole bounding box is the footprint (spec D3): the refusal names the mesh element, its box, and the split."""
    with pytest.raises(RangeError, match=r"start 'in' .*overlaps mesh element 'm', whose footprint is its whole "
                                         r"bounding box \(x 3 to 3.5 m, y 3 to 3.5 m\).*split the element"):
        load_range(str(block_range(tmp_path, start="\n[start.in]\npose = [3.25, 3.25, 0.0]\n")), tmp_path)
    path = block_range(tmp_path)
    path.write_text(path.read_text().replace("at = [[1.0, 1.0]]", "at = [[3.25, 3.25]]"))
    with pytest.raises(RangeError, match=r"ball 0 at \(3.25, 3.25\) lies on mesh element 'm', whose footprint"):
        load_range(str(path), tmp_path)


def test_a_raised_mesh_leaves_a_start_clear(tmp_path):
    path = block_range(tmp_path, "", "\n[start.under]\npose = [3.25, 3.25, 0.0]\n")
    path.write_text(path.read_text().replace("pos = [3, 3, 0]", "pos = [3, 3, 1]"))
    assert "under" in load_range(str(path), tmp_path).starts


def test_the_view_draws_the_mesh(tmp_path):
    w = World(range_cfg(range={"file": str(block_range(tmp_path)), "floor_balls": False}), None)
    v = model_view(w, include_world=True)
    ours = [g for g in v["world_geoms"] if g["name"].startswith("element.m.")]
    assert ours and all(g["type"] == "mesh" and isinstance(g["mesh"], int) for g in ours)


def test_station_boxes_hold_each_part(tmp_path):
    from scrimmage.testrange.station import _element_boxes
    e = mesh_of(tmp_path, CLIMBER + 'select = ["Climber/arm_A"]\npos = [1, 2, 0]\n')
    (c, rot, half), = _element_boxes(e)
    assert c == pytest.approx([1.06, 2.1, 0.41], abs=1e-6) and half == pytest.approx([0.01, 0.01, 0.4], abs=1e-6)
    assert rot == pytest.approx(np.eye(3))


@pytest.mark.skipif(not FIELD_STEP.is_file(), reason="FIRST's field CAD isn't here")
def test_the_field_stage_is_a_mesh_element(tmp_path):
    import re
    import time
    # The whole of GE-24200_Blue is 1.17 million triangles (three 125 000-triangle CHAINS and 750 bolts), over
    # MAX_MESH_TRIANGLES. The element is the how-to's (docs/test-range.md, step 4): the frame's 30 structure pieces,
    # placed as the how-to works out (the file is Y-up, its origin the field's middle, its +x toward blue).
    doc = (Path(__file__).parent.parent / "docs" / "test-range.md").read_text(encoding="utf-8")
    howto = re.search(r'```toml\n(\[\[element\]\]\nname = "stage_blue_frame".*?)```', doc, re.S).group(1)
    body = howto.replace('file = "cad/FE-2024.STEP"', f'file = "{FIELD_STEP.as_posix()}"')
    (tmp_path / "r.toml").write_text(HEAD + 'field = { length = 16.541, width = 8.211 }\n\n' + body)
    rng = load_range("r.toml", tmp_path)
    e = rng.elements[0]
    assert all(2.0 <= x <= 6.0 for x in (e.hi - e.lo)[:2]), e.hi - e.lo
    assert np.hypot(e.center[0] - 4.549, e.center[1] - 4.106) <= 0.15, e.center    # the blue STAGE, not mirrored
    assert not [w for w in rng.warnings if "outside" in w]
    World(range_cfg(range={"file": str(tmp_path / "r.toml"), "floor_balls": False}), None)
    start = time.monotonic()
    load_range("r.toml", tmp_path)
    assert time.monotonic() - start < 2.0


def test_a_damaged_cache_file_is_a_miss(tmp_path):
    body = CLIMBER + 'select = ["Climber/arm_*"]\n'
    first = mesh_of(tmp_path, body)
    files = list((tmp_path / "mesh-cache").glob("*.npz"))
    assert len(files) == 2 and all(".s" in f.name or ".parts" not in f.name for f in files)
    for f in files:
        f.write_bytes(f.read_bytes()[:200])         # cut short
    again = mesh_of(tmp_path, body)
    assert again.pieces == first.pieces and len(again.parts) == len(first.parts)


def test_an_over_cap_selection_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(mesh_module, "MAX_MESH_TRIANGLES", 10)
    with pytest.raises(RangeError):
        load(tmp_path, CLIMBER)
    assert not list((tmp_path / "mesh-cache").glob("*"))


def _tagged(tmp_path, body: str, tags) -> object:
    """The test range with a mesh element and a layout of `tags` as (id, x, y, z, yaw_deg)."""
    import json
    import math

    def pose(x, y, z, yaw):
        h = math.radians(yaw) / 2.0
        return {"translation": {"x": x, "y": y, "z": z},
                "rotation": {"quaternion": {"W": math.cos(h), "X": 0.0, "Y": 0.0, "Z": math.sin(h)}}}
    (tmp_path / "tags.json").write_text(json.dumps({"field": {"length": 16.54, "width": 8.07},
                                                    "tags": [{"ID": t[0], "pose": pose(*t[1:])} for t in tags]}))
    (tmp_path / "r.toml").write_text(element(body).replace("[range]\n", '[range]\napriltags = "tags.json"\n', 1))
    return load_range("r.toml", tmp_path)


def _plate(tmp_path) -> None:
    """A 2 cm x 0.9 m x 0.5 m plate, its +x face's center at its origin."""
    v = np.array([(x, y, z) for x in (-0.02, 0.0) for y in (-0.45, 0.45) for z in (-0.25, 0.25)], dtype=float)
    write_stl(tmp_path / "plate.stl", v, CUBE_F)


def test_a_tag_flush_on_a_turned_mesh_face_is_seen(tmp_path):
    """A tag flush on a mesh plate turned 60 degrees: no warning, and it is drawn on the face, not in front of the
    part's bounding box (0.27 m out)."""
    import math
    from scrimmage.checks import shown
    _plate(tmp_path)
    rng = _tagged(tmp_path, 'file = "plate.stl"\nunits = "m"\npos = [5, 4, 1]\nrpy_deg = [0, 0, 60]\n',
                  [(1, 5.0, 4.0, 1.0, 60.0)])
    assert [w for w in map(shown, rng.warnings) if "tag" in w] == []
    t = rng.apriltags.tag(1)
    n = np.array([math.cos(math.radians(60)), math.sin(math.radians(60)), 0.0])
    out = (np.array(t.face) - [5.0, 4.0, 1.0]) @ n
    assert 0.0 <= out <= 0.002 and np.linalg.norm(np.array(t.face) - [5.0, 4.0, 1.0]) <= 0.002


def test_a_tag_deep_in_a_mesh_part_is_warned_about(tmp_path):
    """0.2 m inside a convex mesh part (a 0.5 m cube), facing out of its +x face: warned about, and left in place."""
    from scrimmage.checks import shown
    write_stl(tmp_path / "block.stl", *cube(0.5))
    rng = _tagged(tmp_path, 'file = "block.stl"\nunits = "m"\npos = [5, 4, 0.75]\n', [(1, 5.3, 4.25, 1.0, 0.0)])
    warned = [w for w in map(shown, rng.warnings) if "tag" in w]
    assert len(warned) == 1 and "tag 1" in warned[0] and "'m'" in warned[0]
    assert rng.apriltags.tag(1).face == pytest.approx((5.3, 4.25, 1.0))
    (tmp_path / "shallow").mkdir()
    write_stl(tmp_path / "shallow" / "block.stl", *cube(0.5))
    rng = _tagged(tmp_path / "shallow", 'file = "block.stl"\nunits = "m"\npos = [5, 4, 0.75]\n',
                  [(1, 5.45, 4.25, 1.0, 0.0)])
    assert [w for w in map(shown, rng.warnings) if "tag" in w] == []
    assert rng.apriltags.tag(1).face == pytest.approx((5.501, 4.25, 1.0), abs=1e-6)


def test_a_mesh_note_is_worded_in_the_language(tmp_path, monkeypatch):
    """cad_factor's note (a STEP whose unit can't be told ignores `units`) is cached by its kind, not its words: read
    from the file or from the cache, it is shown in the Language and its machine text stays the source's English."""
    import json
    import scrimmage.cad.source as source
    from scrimmage import i18n
    from scrimmage.checks import shown
    source_text = ("{where}: can't tell which units {file} is in, so {units_setting} is ignored and the file's own are "
                   "used")
    folder = tmp_path / "locales"
    folder.mkdir()
    (folder / "en-GB.json").write_text(json.dumps({source_text: "{where}: {file}'s units are a mystery, so "
                                                                "{units_setting} is ignored"}))
    shipped = i18n.LOCALES
    monkeypatch.setattr(i18n, "LOCALES", folder)
    i18n.reload()
    try:
        monkeypatch.setattr(source, "step_length_unit", lambda path: None)
        for _ in range(2):      # the read, then the cache
            notes = [w for w in mesh_of(tmp_path, CLIMBER + 'select = ["Climber/arm_*"]\nunits = "mm"\n').warnings
                     if "units" in w]
            assert len(notes) == 1 and "can't tell which units climber.step" in notes[0]
            with i18n.fixed("en-GB"):
                assert "climber.step's units are a mystery, so units = \"mm\" is ignored" in shown(notes[0])
    finally:
        monkeypatch.setattr(i18n, "LOCALES", shipped)
        i18n.reload()


def test_a_perimeter_sized_piece_is_not_a_units_mistake(tmp_path):
    """A piece up to 1 m larger than the field (the 2024 CAD's guardrails are 16.637 m) loads; 2 m larger is refused."""
    v = np.array([(x, y, z) for x in (0, 17.4) for y in (0, 0.05) for z in (0, 0.5)], dtype=float)
    write_stl(tmp_path / "rail.stl", v, CUBE_F)
    e = mesh_of(tmp_path, 'file = "rail.stl"\nunits = "m"\npos = [-0.4, -0.05, 0]\n')
    assert e.hi[0] - e.lo[0] == pytest.approx(17.4)
    assert not [w for w in e.warnings if "outside" in w]
    v[4:, 0] = 18.6
    write_stl(tmp_path / "rail.stl", v, CUBE_F)
    with pytest.raises(RangeError, match="larger than the 16.54 by 8.07 m field"):
        mesh_of(tmp_path, 'file = "rail.stl"\nunits = "m"\n')


def test_an_element_far_outside_the_field_is_warned_about(tmp_path):
    """More than 0.5 m outside the field (a wrong pos or rpy_deg): a warning naming the element and the two keys."""
    from scrimmage.checks import shown
    write_stl(tmp_path / "block.stl", *cube(0.5))
    near = load(tmp_path, 'file = "block.stl"\nunits = "m"\npos = [-0.4, 3, 0]\n')
    assert not [w for w in near.warnings if "outside" in w]
    far = load(tmp_path, 'file = "block.stl"\nunits = "m"\npos = [3, -1.5, 0]\n')
    warned = [shown(w) for w in far.warnings if "outside" in w]
    assert len(warned) == 1 and "element 'm'" in warned[0] and "reaches 1.5 m outside" in warned[0]
    assert "check pos and rpy_deg" in warned[0]
