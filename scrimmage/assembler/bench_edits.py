"""Pure edit operations on a bench file's tomlkit document: set a dotted key, add, remove, rename or override an
entry, and start a new bench. Comments and order are kept (see tomlutil). Keys are the ones `bench/spec.py` reads:
`wheel.top.diameter`, `shaft.top.target_rps`, `run.duration`."""

from __future__ import annotations

import copy
import re
from pathlib import Path

import tomlkit
from tomlkit.items import SingleKey

from ..bench.spec import _LISTS, _SINGLES, BenchError, _check_path, _valid_keys
from .edits import EditError
from .tomlutil import container, insert_aot_item, insert_table, plain, reindex, remove_aot_item, remove_table
from .tomlutil import set_value as _set_value

_NAME_RE = re.compile(r"[A-Za-z0-9_-]+")
_ENTRY_SECTIONS = (*_LISTS, "shaft")
# Where a new top-level table goes when its array does not exist yet: before these.
_AFTER_SHAFTS = (*_LISTS, "flight", "run", "sweep", "chart")

# A minimal valid entry per section (lengths in meters, as the bench reads them).
DEFAULTS: dict[str, dict] = {
    "bench": {"name": "my idea"},
    "piece": {"preset": "2026-fuel"},
    "shaft": {"motor": "KrakenX60", "target_rps": 40, "kP": 0.5, "wheel_inertia": 0.001},
    "wheel": {"center": [0.0, 0.0, 0.1108], "diameter": 0.1016, "width": 0.1, "tread": "40A"},
    "plate": {"center": [-0.22, 0.0, -0.08], "size": [0.26, 0.2, 0.01]},
    "bar": {"a": [0.25, -0.15, -0.058], "b": [0.25, 0.15, -0.058], "diameter": 0.0254},
    "curve": {"center": [0.1, 0.0, 0.0], "radius": 0.3, "start_deg": 0, "end_deg": 90, "width": 0.2},
    "cad": {"mesh": "part.stl", "units": "mm"},   # the file has to exist before the bench loads
    "inlet": {"center": [-0.25, 0.0, 0.001], "size": [0.001, 0.001, 0.001], "count": 3, "rate": 3,
              "velocity": [2.0, 0.0, 0.0]},
    "outlet": {"center": [0.9, 0.0, 0.5], "size": [1.0, 1.0, 1.0]},
    "flight": {"outlet": "goal"},
    "run": {"duration": 2.5},
}


def _err(field: str, message: str) -> EditError:
    return EditError(field, message)


def _check_key(doc, dotted: str) -> tuple[str, str | None, str]:
    """(section, entry name, key) of a dotted key, or EditError naming it. Entries must be in this file."""
    raw = plain_dict(doc)
    try:
        return _check_path(raw, dotted)
    except BenchError as e:
        msg = str(e)
        if "no " in msg and " named " in msg:
            msg += "; if it comes from an included file, override it first"
        raise _err(dotted, msg) from None


def plain_dict(doc) -> dict:
    return doc.unwrap() if hasattr(doc, "unwrap") else dict(doc)


def _entry(doc, section: str, name: str):
    """The table of entry `name` in this file, or None."""
    if section == "shaft":
        sh = doc.get("shaft")
        return sh[name] if sh is not None and name in sh else None
    return next((e for e in doc.get(section, []) if e.get("name") == name), None)


def _names(doc, section: str) -> list[str]:
    if section == "shaft":
        return list(doc["shaft"]) if "shaft" in doc else []
    return [e.get("name") for e in doc.get(section, [])]


def _require(doc, section: str, name: str):
    if section not in _ENTRY_SECTIONS:
        raise _err(section, f"unknown section '{section}'; valid sections: {', '.join(_ENTRY_SECTIONS)}")
    t = _entry(doc, section, name)
    if t is None:
        raise _err(f"{section}.{name}", f"no {section} named '{name}' in this file; if it comes from an included "
                                         "file, override it first")
    return t


def _twin(key: str) -> str:
    return key[:-3] if key.endswith("_in") else key + "_in"


def _single_table(doc, section: str):
    if section not in doc:
        t = tomlkit.table()
        insert_table(doc, section, t)
    return doc[section]


def _like(old, new):
    """`new` with whole numbers made floats where `old` (the value it replaces) has floats: JSON sends 0.0 as 0."""
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [_like(o, n) for o, n in zip(old, new)]
    if isinstance(old, float) and isinstance(new, int) and not isinstance(new, bool):
        return float(new)
    return new


def set_value(doc, key: str, value) -> None:
    """Set the dotted `key`; None removes it. Setting `x` drops `x_in` and the other way round."""
    section, name, k = _check_key(doc, key)
    if name is None:
        if value is None and section not in doc:
            return
        table = _single_table(doc, section)
    else:
        table = _entry(doc, section, name)
    twin = _twin(k)
    if value is None:
        table.pop(k, None)
        return
    old = table.get(k, table.get(twin))
    if old is not None:
        value = _like(old.unwrap() if hasattr(old, "unwrap") else old, value)
    if twin in table:
        if k in table:
            table.pop(twin)
        else:   # take the twin's place, with its comment
            _set_value(table, k, plain(value), (twin,))
            table.pop(twin)
            return
    _set_value(table, k, plain(value))


def _unique(doc, section: str, base: str | None) -> str:
    names = _names(doc, section)
    if base is not None:
        if not isinstance(base, str) or not _NAME_RE.fullmatch(base):
            raise _err(f"{section}.{base}", "use letters, digits, '_' or '-' only")
        if base in names:
            raise _err(f"{section}.{base}", f"a {section} named {base!r} already exists")
        return base
    n = 1
    while f"{section}{n}" in names:
        n += 1
    return f"{section}{n}"


def _fill(section: str, fields: dict | None) -> dict:
    out = dict(DEFAULTS[section])
    if section == "cad" and "part" in (fields or {}):   # a part folder in place of the default mesh (and its units)
        out.pop("mesh")
        out.pop("units")
    for k, v in (fields or {}).items():
        if k not in _valid_keys(section) or k == "name":
            raise _err(f"{section}.{k}", f"unknown key '{k}' for {section}; valid keys: {', '.join(_valid_keys(section))}")
        out.pop(_twin(k), None)
        out[k] = v
    return out


def _shaft_table(values: dict):
    t = tomlkit.table()
    for k, v in values.items():
        t[k] = plain(v)
    return t


def _add_shaft(doc, name: str, values: dict) -> None:
    t = _shaft_table(values)
    if "shaft" in doc:
        insert_table(doc["shaft"], name, t)
        return
    sup = tomlkit.table(is_super_table=True)
    sup.add(name, t)
    insert_table(doc, "shaft", sup, before=_AFTER_SHAFTS)


def add_entry(doc, section: str, name: str | None = None, fields: dict | None = None) -> str:
    """Add an entry and return its name. A wheel in a file without shafts gets a shaft of its own."""
    if section not in _ENTRY_SECTIONS:
        raise _err(section, f"unknown section '{section}'; valid sections: {', '.join(_ENTRY_SECTIONS)}")
    name = _unique(doc, section, name)
    values = _fill(section, fields)
    if section == "shaft":
        _add_shaft(doc, name, values)
        return name
    t = tomlkit.table()
    t["name"] = name
    if section == "wheel" and "shaft" not in values:
        on = {w.get("shaft") for w in doc.get("wheel", [])}
        free = [s for s in _names(doc, "shaft") if s not in on]
        if not free:   # wheels on one shaft turn on one axis, so a new wheel gets a shaft of its own
            sname = name if name not in _names(doc, "shaft") else _unique(doc, "shaft", None)
            _add_shaft(doc, sname, DEFAULTS["shaft"])
            free = [sname]
        t["shaft"] = free[0]
    for k, v in values.items():
        t[k] = plain(v)
    insert_aot_item(doc, section, t, before=_after_lists(section))
    return name


def _after_lists(section: str) -> tuple[str, ...]:
    later = _LISTS[_LISTS.index(section) + 1:]
    return (*later, "flight", "run", "sweep", "chart")


def _users(doc, section: str, name: str) -> list[str]:
    """Descriptions of what in the file refers to this entry by name."""
    out: list[str] = []
    if section == "shaft":
        out += [f"wheel '{w.get('name')}'" for w in doc.get("wheel", []) if w.get("shaft") == name]
        for c in doc.get("cad", []):
            if name in dict(c.get("slots", {})).values():
                out.append(f"CAD part '{c.get('name')}'")
        out += [f"shaft '{n}' (coupled_to)" for n, s in doc.get("shaft", {}).items() if s.get("coupled_to") == name]
    if section == "outlet" and doc.get("flight", {}).get("outlet") == name:
        out.append("[flight]")
    return out


def _sweep_keys(doc, section: str, name: str) -> list[str]:
    sweep = doc.get("sweep")
    return [k for k in (sweep or {}) if k.startswith(f"{section}.{name}.")]


def remove_entry(doc, section: str, name: str) -> None:
    """Remove an entry (and any [sweep] keys on it); refused while a wheel, CAD slot, coupling or flight uses it."""
    _require(doc, section, name)
    field = f"{section}.{name}"
    users = _users(doc, section, name)
    if users:
        raise _err(field, f"{section} '{name}' is used by {', '.join(users)}; change those first")
    for k in _sweep_keys(doc, section, name):
        del doc["sweep"][k]
    if section == "shaft":
        remove_table(doc["shaft"], name)
        if not len(doc["shaft"]):
            del doc["shaft"]
    else:
        i = next(n for n, e in enumerate(doc[section]) if e.get("name") == name)
        remove_aot_item(doc, section, i)


def _rename_key(table, old: str, new: str) -> None:
    """Give a table key a new name in place (keeping its position, value and comment)."""
    c = container(table)
    for i, (k, v) in enumerate(c.body):
        if k is not None and k.key == old:
            c.body[i] = (SingleKey(new), v)
            if hasattr(v, "display_name"):
                v.display_name = None   # a table's header would otherwise keep the old name
    reindex(c)
    dict.clear(c)   # keep the dict's order the file's order
    for k, v in c.body:
        if k is not None:
            dict.__setitem__(c, k.key, v)


def rename_entry(doc, section: str, name: str, new_name: str) -> None:
    """Rename an entry and everything in the file that names it: wheel.shaft, CAD slots, coupled_to,
    flight.outlet and [sweep] keys."""
    t = _require(doc, section, name)
    field = f"{section}.{name}"
    if not isinstance(new_name, str) or not _NAME_RE.fullmatch(new_name):
        raise _err(field, "use letters, digits, '_' or '-' only")
    if new_name == name:
        return
    if new_name in _names(doc, section):
        raise _err(field, f"a {section} named {new_name!r} already exists")
    if section == "shaft":
        _rename_key(doc["shaft"], name, new_name)
        for w in doc.get("wheel", []):
            if w.get("shaft") == name:
                _set_value(w, "shaft", new_name)
        for c in doc.get("cad", []):
            slots = c.get("slots")
            for slot, s in list((slots or {}).items()):
                if s == name:
                    _set_value(slots, slot, new_name)
        for s in doc["shaft"].values():
            if s.get("coupled_to") == name:
                _set_value(s, "coupled_to", new_name)
    else:
        _set_value(t, "name", new_name)
        if section == "outlet" and doc.get("flight", {}).get("outlet") == name:
            _set_value(doc["flight"], "outlet", new_name)
    sweep = doc.get("sweep")
    for k in _sweep_keys(doc, section, name):
        _rename_key(sweep, k, f"{section}.{new_name}." + k.split(".", 2)[2])


def override_included(doc, raw_merged: dict, section: str, name: str) -> None:
    """Copy an entry that comes from an included file into this one, so this file's own copy wins (and can then
    be edited). `raw_merged` is the file after includes; a single section ([piece], [run], [flight]) is copied whole."""
    if section in _SINGLES:
        if section in doc:
            raise _err(section, f"[{section}] is already in this file")
        src = raw_merged.get(section)
        if src is None:
            raise _err(section, f"no [{section}] to override")
        t = tomlkit.table()
        for k, v in copy.deepcopy(src).items():
            t[k] = plain(v)
        insert_table(doc, section, t)
        return
    if section not in _ENTRY_SECTIONS:
        raise _err(section, f"unknown section '{section}'; valid sections: {', '.join(_ENTRY_SECTIONS)}")
    field = f"{section}.{name}"
    if _entry(doc, section, name) is not None:
        raise _err(field, f"{section} '{name}' is already in this file")
    if section == "shaft":
        src = raw_merged.get("shaft", {}).get(name)
    else:
        src = next((e for e in raw_merged.get(section, []) if e.get("name") == name), None)
    if src is None:
        raise _err(field, f"no {section} named '{name}' in the included files")
    src = copy.deepcopy(src)
    if section == "shaft":
        _add_shaft(doc, name, src)
        return
    t = tomlkit.table()
    t["name"] = name
    for k, v in src.items():
        if k != "name":
            t[k] = plain(v)
    insert_aot_item(doc, section, t, before=_after_lists(section))


_BLANK = '''# {title}
#
#   scrimmage bench run <this file>

[bench]
name = ""
author = ""
idea = ""

[piece]
preset = "2026-fuel"             # 0.15 m foam ball

[shaft.bottom]
motor = "KrakenX60"
target_rps = 30
kP = 0.3
wheel_inertia = 0.001

[shaft.top]
motor = "KrakenX60"
target_rps = 30
kP = 0.3
wheel_inertia = 0.001

# two 4 in. wheels 0.12 m apart at the nip: the ball is squeezed 0.03 m, 0.2 of its diameter
[[wheel]]
name = "bottom"
shaft = "bottom"
center = [0.0, 0.0, -0.1108]
diameter = 0.1016
width = 0.1
tread = "40A"

[[wheel]]
name = "top"
shaft = "top"
center = [0.0, 0.0, 0.1108]
axis = [0.0, -1.0, 0.0]          # turned the other way, so both treads drive the ball forward
diameter = 0.1016
width = 0.1
tread = "40A"

[[plate]]
name = "feeder"
center = [-0.22, 0.0, -0.08]     # the ball rolls on it into the nip
size = [0.26, 0.2, 0.01]

[[inlet]]
name = "feed"
center = [-0.25, 0.0, 0.001]
size = [0.001, 0.001, 0.001]
count = 3
rate = 3
velocity = [2.0, 0.0, 0.0]

[[outlet]]
name = "goal"
center = [0.9, 0.0, 0.5]
size = [1.0, 1.0, 1.0]

[run]
duration = 2.5
'''


def new_bench_text(name: str, author: str, idea: str, example: Path | None) -> str:
    """The text of a new bench file: a copy of `example` (its comments and shapes) or a blank two-wheel pinch, with
    [bench] name, author and idea set."""
    text = _BLANK.format(title="A new idea: edit anything, then run it.") if example is None else \
        Path(example).read_text(encoding="utf-8")
    doc = tomlkit.parse(text)
    if "bench" not in doc:
        t = tomlkit.table()
        t["name"] = ""
        doc.add("bench", t)
    for k, v in (("name", name), ("author", author), ("idea", idea)):
        _set_value(doc["bench"], k, v, ("name", "author"))
    return tomlkit.dumps(doc)
