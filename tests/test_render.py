import tomllib
from pathlib import Path

import numpy as np
import pytest

from scrimmage.render import Renderer, RenderUnavailable, contact_sheet, to_png
from scrimmage.world import World

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def world():
    return World(tomllib.loads((ROOT / "configs" / "kitbot.toml").read_text()), ROOT / "configs")


def test_follow_and_top_views_are_not_blank(world):
    r = Renderer(world)
    try:
        for view in ("follow", "top"):
            img = r.render(view, width=320, height=200)
            assert img.shape == (200, 320, 3) and img.std() > 5, view
    finally:
        r.close()


def test_paths_are_drawn(world):
    r = Renderer(world)
    try:
        plain = r.render("top", width=480, height=300).astype(int)
        drawn = r.render("top", width=480, height=300,
                         paths={"truth": [(2.0, 4.0), (10.0, 4.0)], "odometry": [(2.0, 2.0), (10.0, 2.0)]}).astype(int)
        changed = np.abs(drawn - plain).sum(axis=2) > 60
        assert changed.sum() > 200
        greenish = drawn[changed]
        assert (greenish[:, 1] > greenish[:, 0]).any() and (greenish[:, 0] > greenish[:, 1]).any()  # both colors
    finally:
        r.close()


def test_large_images_and_free_view(world):
    r = Renderer(world)
    try:
        assert r.render("free", width=1920, height=1200,
                        camera={"lookat": [8, 4, 0], "distance": 10}).shape == (1200, 1920, 3)
        with pytest.raises(ValueError, match="lookat"):
            r.render("free")
    finally:
        r.close()


def test_named_cameras(tmp_path, world):
    r = Renderer(world)
    try:
        with pytest.raises(ValueError, match="the model has no cameras"):
            r.render("camera:front")
        with pytest.raises(ValueError, match='"follow", "top"'):
            r.render("sideways")
    finally:
        r.close()
    xml = (ROOT / "tests" / "models" / "kitbot.xml").read_text().replace(
        '<freejoint name="chassis_free"/>', '<freejoint name="chassis_free"/>\n<camera name="front" pos="0.4 0 0.5" xyaxes="0 -1 0 0 0 1"/>')
    (tmp_path / "cam.xml").write_text(xml)
    cfg = tomllib.loads((ROOT / "tests" / "fake_robot.toml").read_text())
    cfg["model"]["mjcf"] = str(tmp_path / "cam.xml")
    w2 = World(cfg, ROOT / "tests")
    r2 = Renderer(w2)
    try:
        assert r2.render("camera:front", width=160, height=100).std() > 1
    finally:
        r2.close()


def test_contact_sheet_layout_and_png():
    frames = [(i * 0.25, np.full((50, 80, 3), 40 * i, np.uint8)) for i in range(6)]
    sheet = contact_sheet(frames, columns=4)
    assert sheet.shape == (100, 320, 3)
    assert to_png(sheet)[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_unavailable_message(world, monkeypatch):
    import mujoco

    def boom(*a, **k):
        raise RuntimeError("no GL")
    monkeypatch.setattr(mujoco, "Renderer", boom)
    with pytest.raises(RenderUnavailable, match="MUJOCO_GL=egl"):
        Renderer(world).render("follow")


def test_bad_sizes_and_camera_dicts(world):
    r = Renderer(world)
    try:
        for kw in ({"width": 0}, {"height": 5000}, {"width": True}, {"width": 1.5}):
            with pytest.raises(ValueError, match="whole number from 16 to 4096"):
                r.render("top", **kw)
        with pytest.raises(ValueError, match="lookat must be a list of 3"):
            r.render("free", camera={"lookat": [1, 2]})
        with pytest.raises(ValueError, match="unknown camera key.*zoom.*allowed: lookat"):
            r.render("free", camera={"lookat": [1, 2, 0], "zoom": 3})
        with pytest.raises(ValueError, match="distance must be a number"):
            r.render("free", camera={"lookat": [1, 2, 0], "distance": "far"})
    finally:
        r.close()


def test_one_renderer_per_size_is_kept_and_prepared(world, monkeypatch):
    """Building a GL renderer holds Python's GIL (seconds with slow software GL), so switching sizes must not rebuild,
    and prepare() builds them up front."""
    import scrimmage.render as render_mod
    built = []
    real = render_mod.mujoco.Renderer

    def counting(*a, **kw):
        built.append((kw["width"], kw["height"]))
        return real(*a, **kw)
    monkeypatch.setattr(render_mod.mujoco, "Renderer", counting)
    r = Renderer(world)
    try:
        r.prepare([(160, 100), (80, 50)])
        assert built == [(160, 100), (80, 50)]
        for size in ((160, 100), (80, 50), (160, 100), (80, 50)):
            assert r.render("top", width=size[0], height=size[1]).shape == (size[1], size[0], 3)
        assert built == [(160, 100), (80, 50)]                  # no rebuild when switching sizes
        for w in (40, 48, 56, 64):                              # a fifth size evicts the least recently used
            r.render("top", width=w, height=30)
        assert len(r._rs) == Renderer.KEEP and (160, 100) not in r._rs
    finally:
        r.close()
