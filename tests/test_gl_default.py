import sys

import pytest

from scrimmage import _gl


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the default is only chosen on Linux")
@pytest.mark.parametrize("libs, want", [({"EGL", "OSMesa"}, "egl"), ({"OSMesa"}, "osmesa"), (set(), None)])
def test_default_backend_prefers_what_is_installed(libs, want):
    assert _gl.default_backend(find=lambda name: f"lib{name}.so" if name in libs else None) == want


def test_a_chosen_backend_is_kept(monkeypatch):
    monkeypatch.setenv("MUJOCO_GL", "glfw")
    _gl.set_default()
    assert __import__("os").environ["MUJOCO_GL"] == "glfw"
