"""The AprilTag layout of a range (scrimmage/testrange/apriltags.py, spec D5): read from WPILib's JSON, checked against
the range's field, set into its mounts (R6), built as visual-only geoms, listed by `range check` and sent to the live
view."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from range_fixtures import KITBOT, range_2024_with_tags, range_cfg, spares_world  # noqa: E402
from scrimmage.assembler.view import model_view  # noqa: E402
from scrimmage.blocks import palette  # noqa: E402
from scrimmage.protocol import HalState  # noqa: E402
from scrimmage.sensors.runtime import SensorRuntime  # noqa: E402
from scrimmage.sensors.spec import parse_sensors  # noqa: E402
from scrimmage.testrange import apriltags, check  # noqa: E402
from scrimmage.testrange.spec import BUILTIN_RANGES, RangeError, load_range  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RANGE_2026 = BUILTIN_RANGES["2026"]


def tag_json(tags, length=16.54, width=8.07) -> str:
    """Tags as (id, x, y, z) or (id, x, y, z, yaw_deg)."""
    def pose(x, y, z, yaw=0.0):
        h = math.radians(yaw) / 2.0
        return {"translation": {"x": x, "y": y, "z": z},
                "rotation": {"quaternion": {"W": math.cos(h), "X": 0.0, "Y": 0.0, "Z": math.sin(h)}}}
    return json.dumps({"field": {"length": length, "width": width},
                       "tags": [{"ID": t[0], "pose": pose(*t[1:])} for t in tags]})


def range_with(tmp_path, tags=((1, 8.0, 4.0, 1.0), (2, 9.0, 4.0, 1.0)), *, layout=None, size=None, extra="",
               length=16.54, width=8.07, base="2026") -> Path:
    """A copy of the 2026 range with `apriltags` set: a layout JSON of `tags` in tmp_path, or the text `layout`."""
    if layout is None:
        (tmp_path / "tags.json").write_text(tag_json(tags, length, width), encoding="utf-8")
        layout = "tags.json"
    text = BUILTIN_RANGES[base].read_text(encoding="utf-8")
    lines = f'apriltags = "{layout}"\n' + (f"apriltag_size_in = {size}\n" if size is not None else "") + extra
    out = tmp_path / "r.toml"
    out.write_text(text.replace("\n[range]\n", "\n[range]\n" + lines, 1), encoding="utf-8")
    return out


def load(path: Path):
    return load_range(path.name, path.parent)


def test_the_built_in_layouts_are_shipped():
    assert set(apriltags.BUILTIN_LAYOUTS) == {"2024-crescendo", "2026-rebuilt-welded", "2026-rebuilt-andymark"}
    assert all(p.is_file() for p in apriltags.BUILTIN_LAYOUTS.values())
    assert (apriltags.BUILTIN_LAYOUTS["2024-crescendo"].parent / "LICENSE.md").is_file()


def test_2024_layout_reads_and_matches_the_range(tmp_path):
    rng = load(range_2024_with_tags(tmp_path))
    lay = rng.apriltags
    assert lay.name == "2024-crescendo" and len(lay.tags) == 16 and lay.ids == list(range(1, 17))
    assert lay.size == pytest.approx(6.5 * 0.0254)
    t7 = lay.tag(7)
    assert t7.pos == pytest.approx((-0.0381, 5.5479, 1.4511), abs=1e-3)
    assert t7.normal == pytest.approx([1.0, 0.0, 0.0], abs=1e-9)
    speaker_y = next(g for g in rng.goals if g.name == "speaker_blue").pos[1]
    assert speaker_y == pytest.approx(5.5446) and abs(t7.pos[1] - speaker_y) < 0.01
    assert lay.tag(8).pos[1] == pytest.approx(4.9827, abs=1e-3)


def test_2024_mounted_tags_face_the_field(tmp_path):
    """Review Focus 1: tag 7's center is 3.8 cm inside the SPEAKER wall obstacle; its drawn square is in front."""
    rng = load(range_2024_with_tags(tmp_path))
    lay = rng.apriltags
    assert 0.0 < lay.tag(7).face[0] <= 0.002
    for i in (11, 12, 15, 16):      # the stage cores' tags: 7 cm inside the estimated core, drawn on its face
        t = lay.tag(i)
        out = np.array(t.face) - np.array(t.pos)
        assert 0.07 < out @ t.normal <= apriltags.MOUNT_DEPTH, i
        assert np.allclose(out, (out @ t.normal) * t.normal)
    assert rng.warnings == ()


def _error(tmp_path, **kw) -> str:
    with pytest.raises(RangeError) as e:
        load(range_with(tmp_path, **kw))
    assert "[range]" in str(e.value)
    return str(e.value)


def test_the_layout_checks(tmp_path):
    msg = _error(tmp_path, length=16.541, width=8.211, base="2026")        # a 2024-size layout on a 2026-size range
    assert "16.541" in msg and "8.211" in msg and "16.54" in msg and "8.07" in msg
    (tmp_path / "a").mkdir()
    msg = _error(tmp_path / "a", tags=[(1, 17.0, 4.0, 1.0)])
    assert "tag 1" in msg and "outside" in msg
    (tmp_path / "b").mkdir()
    msg = _error(tmp_path / "b", tags=[(3, 8.0, 4.0, 1.0), (3, 9.0, 4.0, 1.0)])
    assert "tag 3 twice" in msg
    (tmp_path / "c").mkdir()
    assert "no.json" in _error(tmp_path / "c", layout="no.json")
    (tmp_path / "d").mkdir()
    msg = _error(tmp_path / "d", layout="2023")
    assert all(name in msg for name in apriltags.BUILTIN_LAYOUTS)
    (tmp_path / "e").mkdir()
    assert "apriltag_size_in" in _error(tmp_path / "e", size=0)
    (tmp_path / "e2").mkdir()
    assert "apriltag_size_in" in _error(tmp_path / "e2", size="inf")
    (tmp_path / "f").mkdir()
    (tmp_path / "f" / "bad.json").write_text("{not json", encoding="utf-8")
    assert "bad.json" in _error(tmp_path / "f", layout="bad.json")
    (tmp_path / "g").mkdir()
    (tmp_path / "g" / "bad.json").write_text(json.dumps({"field": {"length": 16.54, "width": 8.07}, "tags": [
        {"ID": 4, "pose": {"translation": {"x": 1, "y": 1, "z": 1}}}]}), encoding="utf-8")
    msg = _error(tmp_path / "g", layout="bad.json")
    assert "bad.json" in msg and "tag 4" in msg
    (tmp_path / "h").mkdir()
    (tmp_path / "h" / "bad.json").write_text(json.dumps({"tags": []}), encoding="utf-8")
    assert "field" in _error(tmp_path / "h", layout="bad.json")
    # a tag 0.2 m outside a wall (wall-mounted) is fine
    (tmp_path / "i").mkdir()
    assert len(load(range_with(tmp_path / "i", tags=[(1, -0.2, 4.0, 1.0)])).apriltags.tags) == 1


def test_a_tag_deep_in_a_solid_is_warned_about(tmp_path):
    """A tag 0.2 m inside a box obstacle (facing +x, out of its +x face 0.2 m away) gets one warning."""
    obstacle = ('\n[[obstacle]]\nname = "crate"\npos = [5.0, 4.0, 0.5]\nsize = [1.0, 1.0, 1.0]\n')
    path = range_with(tmp_path, tags=[(1, 5.3, 4.0, 0.5), (2, 9.0, 4.0, 1.0)])
    path.write_text(path.read_text(encoding="utf-8") + obstacle, encoding="utf-8")
    rng = load(path)
    from scrimmage.checks import shown
    warned = [shown(w) for w in rng.warnings if "tag" in shown(w)]
    assert len(warned) == 1 and "tag 1" in warned[0] and "crate" in warned[0]


def test_the_2026_layouts_fit_the_2026_field(tmp_path):
    for i, name in enumerate(("2026-rebuilt-welded", "2026-rebuilt-andymark")):
        (tmp_path / str(i)).mkdir()
        rng = load(range_with(tmp_path / str(i), layout=name))
        assert len(rng.apriltags.tags) == 32 and rng.apriltags.name == name


def test_tags_add_no_contacts_and_no_readings(tmp_path):
    base = range_cfg(range={"file": str(range_with(tmp_path)), "floor_balls": False})
    plain = range_cfg(range={"file": str(RANGE_2026), "floor_balls": False})
    w, w0 = spares_world(base), spares_world(plain)
    m = w.model
    for name in ("apriltag.1", "apriltag.1.border", "apriltag.2", "apriltag.2.border"):
        g = m.geom(name)
        assert int(g.contype[0]) == 0 and int(g.conaffinity[0]) == 0 and int(g.group[0]) == 1
    assert m.ngeom == w0.model.ngeom + 4

    from test_sensors import attach, body_dir, body_point, step    # noqa: E402
    reads = []
    for world in (w, w0):
        # a sensor 0.5 m before the tag at (8, 4, 1) points at it; something farther along (the red hub) is what it reads
        sensor = {"name": "s", "kind": "distance", "body": "drive.chassis", "sees": "anything", "max_range": 20.0,
                  "at": body_point(world, "drive.chassis", [7.5, 4.0, 1.0]),
                  "toward": body_dir(world, "drive.chassis", [1.0, 0.0, 0.0])}
        attach(world, sensor)
        step(world)
        reads.append(world.sensors.reading("s").distance)
    assert reads[0] == pytest.approx(reads[1]) and reads[0] > 2.0, "the tags 0.5 and 1.5 m ahead are looked through"

    rng = load(range_with(tmp_path))
    assert any("2 AprilTags" in line for line in check.summary("r.toml", rng))
    assert not [n for n in palette._targets(w, set()) if n.startswith("apriltag.")]
    assert any(n.startswith("wall_") for n in palette._targets(w, set()))


def test_the_view_labels_tags(tmp_path):
    w = spares_world(range_cfg(range={"file": str(range_with(tmp_path)), "floor_balls": False}))
    view = model_view(w, include_world=True)
    assert [t["id"] for t in view["apriltags"]] == [1, 2]
    t = view["apriltags"][0]
    assert t["face"] == pytest.approx([8.0, 4.0, 1.0])
    assert t["quat"] == [1.0, 0.0, 0.0, 0.0] and t["size"] == pytest.approx(0.1651)
    assert model_view(spares_world(range_cfg()), include_world=True)["apriltags"] == []


def test_the_docs_two_tag_example_loads(tmp_path):
    import re
    doc = (ROOT / "docs" / "test-range.md").read_text(encoding="utf-8")
    block = re.search(r"### AprilTags.*?```json\n(.*?)```", doc, re.S).group(1)
    (tmp_path / "practice-tags.json").write_text(block, encoding="utf-8")
    rng = load(range_with(tmp_path, layout="practice-tags.json"))
    assert rng.apriltags.ids == [1, 2]
    assert rng.apriltags.tag(1).normal == pytest.approx([1, 0, 0]) and rng.apriltags.tag(2).normal == pytest.approx([-1, 0, 0])
    assert rng.apriltags.tag(1).face == pytest.approx(rng.apriltags.tag(1).pos)    # above the 0.5 m wall: not inside it


def _with_crate(tmp_path, tags, rpy="[0.0, 0.0, 0.0]"):
    path = range_with(tmp_path, tags=tags)
    path.write_text(path.read_text(encoding="utf-8") + f'\n[[obstacle]]\nname = "crate"\npos = [5.0, 4.0, 0.5]\n'
                    f"size = [1.0, 1.0, 1.0]\nrpy_deg = {rpy}\n", encoding="utf-8")
    return load(path)


def _tag_warnings(rng):
    from scrimmage.checks import shown
    return [shown(w) for w in rng.warnings if "tag" in shown(w)]


def test_a_tag_in_a_yawed_box_is_pushed_out_of_its_face(tmp_path):
    """The depth is measured in the box's own frame: a crate turned 30 degrees, a tag 5 cm inside its +x face."""
    n = np.array([math.cos(math.radians(30)), math.sin(math.radians(30)), 0.0])
    center = np.array([5.0, 4.0, 0.5])
    p = center + n * 0.45
    rng = _with_crate(tmp_path, [(1, *p, 30.0)], rpy="[0.0, 0.0, 30.0]")
    t = rng.apriltags.tag(1)
    assert t.normal == pytest.approx(n)
    assert (np.array(t.face) - center) @ n == pytest.approx(0.501)
    assert _tag_warnings(rng) == []


def test_the_mount_depth_boundary(tmp_path):
    """0.09 m inside a solid: pushed out, no warning. 0.11 m inside: left where it is, and warned about."""
    rng = _with_crate(tmp_path, [(1, 5.41, 4.0, 0.5)])
    assert rng.apriltags.tag(1).face[0] == pytest.approx(5.501) and _tag_warnings(rng) == []
    (tmp_path / "deep").mkdir()
    rng = _with_crate(tmp_path / "deep", [(1, 5.39, 4.0, 0.5)])
    assert rng.apriltags.tag(1).face == pytest.approx((5.39, 4.0, 0.5))
    assert len(_tag_warnings(rng)) == 1


def test_a_tag_behind_a_perimeter_wall_is_not_warned_about(tmp_path):
    """Tag 1 faces +y from inside the blue wall, farther in (along its normal) than MOUNT_DEPTH: no warning."""
    rng = load(range_with(tmp_path, tags=[(1, -0.05, 4.0, 0.2, 90.0)]))
    assert _tag_warnings(rng) == []


def test_a_size_without_a_layout_is_warned_about(tmp_path):
    path = range_with(tmp_path, layout="x.json", size=5)
    path.write_text(path.read_text(encoding="utf-8").replace('apriltags = "x.json"\n', ""), encoding="utf-8")
    rng = load(path)
    assert rng.apriltags is None
    from scrimmage.checks import shown
    assert [w for w in map(shown, rng.warnings) if "apriltag_size_in" in w and "[range]" in w]
