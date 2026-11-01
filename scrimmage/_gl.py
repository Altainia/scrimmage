"""The default headless GL backend for MuJoCo, chosen before mujoco is imported."""

from __future__ import annotations

import ctypes.util
import os
import sys


def default_backend(find=ctypes.util.find_library) -> str | None:
    """EGL when its library is installed (works without a display), else OSMesa (software), else None: MuJoCo's own
    default. Forcing EGL where libEGL is missing makes `import mujoco` itself fail."""
    if not sys.platform.startswith("linux"):
        return None
    for backend, lib in (("egl", "EGL"), ("osmesa", "OSMesa")):
        if find(lib):
            return backend
    return None


def set_default() -> None:
    """Set MUJOCO_GL unless the user already chose one."""
    if "MUJOCO_GL" not in os.environ:
        backend = default_backend()
        if backend:
            os.environ["MUJOCO_GL"] = backend
