"""part.toml text: a small writer that needs only the standard library, so FreeCAD's Python can use it."""

from __future__ import annotations

import json


def _toml_value(v) -> str:
    """One value on one line: scalars, arrays, and inline tables (for geom and joint)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)                      # shortest round-trip form; always a valid TOML float here
    if isinstance(v, str):
        # JSON string escapes are valid TOML basic-string escapes; TOML also forbids a raw DEL
        return json.dumps(v, ensure_ascii=False).replace("\x7f", "\\u007f")
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {_toml_value(x)}" for k, x in v.items()) + " }" if v else "{}"
    raise TypeError(f"can't write {v!r} to part.toml")


def dumps(d: dict) -> str:
    """part.toml text for the data from part_to_dict: [part] first, then one [[section]] table per entry."""
    lines = ["[part]"] + [f"{k} = {_toml_value(v)}" for k, v in d["part"].items()]
    for section in ("body", "loop", "coupling", "motor", "encoder", "gyro", "mount"):
        for item in d.get(section, []):
            lines += ["", f"[[{section}]]"] + [f"{k} = {_toml_value(v)}" for k, v in item.items()]
    return "\n".join(lines) + "\n"
