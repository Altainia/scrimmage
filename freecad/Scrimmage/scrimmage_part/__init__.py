"""Scrimmage-sim part exporter for FreeCAD (pure Python; imports only FreeCAD's modules and the stdlib)."""

import os
import re

MIN_FREECAD = (1, 1)


def addon_dir() -> str:
    """The add-on folder (the one holding package.xml and icons/), found from this package's location."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def version_problem(version):
    """None when `version` (FreeCAD.Version(): ["1", "1", "1", ...]) is FreeCAD 1.1 or newer, else the sentence
    saying so. A version that can't be read passes."""
    try:
        have = tuple(int(re.match(r"\d+", str(v)).group()) for v in version[:2])
    except (AttributeError, TypeError, ValueError, IndexError):
        return None
    if len(have) < 2 or have >= MIN_FREECAD:
        return None
    shown = ".".join(str(v) for v in version[:3])
    return f"the scrimmage add-on needs FreeCAD {MIN_FREECAD[0]}.{MIN_FREECAD[1]} or newer (this is {shown})"


def require_freecad():
    """Raise ExportError when the running FreeCAD is older than 1.1."""
    import FreeCAD
    from .errors import ExportError
    p = version_problem(FreeCAD.Version())
    if p:
        raise ExportError([p])
