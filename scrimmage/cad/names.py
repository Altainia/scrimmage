"""Valid body and part names from CAD piece labels (the same rule as the FreeCAD add-on's `sanitize`, plus a few
guards so a name is always usable as a TOML key and a folder name)."""

import re


def sanitize(label: str) -> str:
    """`label` with every run of characters outside [A-Za-z0-9_] turned into one `_`, trimmed of leading and
    trailing `_`; a leading digit gets `p_`, and nothing left becomes `piece`."""
    name = re.sub(r"[^A-Za-z0-9_]+", "_", label).strip("_")
    if not name:
        return "piece"
    return "p_" + name if name[0].isdigit() else name


def unique(base: str, used: set[str]) -> str:
    """`base`, else `base_2`, `base_3`, ... whichever isn't in `used`; the result is added to `used`."""
    name, n = base, 2
    while name in used:
        name = f"{base}_{n}"
        n += 1
    used.add(name)
    return name
