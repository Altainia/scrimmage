"""Pure edit operations on a robot config's tomlkit document: its [[part]] tables, and the robot code panel's
[robot_program], start ([robot] start_pose, [range]) and [battery].

`cad_offset` is where a part built from a CAD import attaches so it sits where the CAD has it (the add op's `pos`
when none is given, and the Placement panel's "Reset to CAD")."""

from __future__ import annotations

import math
import re
from typing import Any

import tomlkit
from tomlkit.items import InlineTable, Table, Whitespace

from .. import i18n
from ..parts.assemble import PLACEMENT_KEYS
from ..testrange.spec import BUILTIN_RANGES
from ..testrange.spec import FIELD_LENGTH, FIELD_WIDTH   # m: the generated field, the blue alliance wall at x = 0
from .tomlutil import plain as _plain
from .tomlutil import ensure_newline_end, remove_aot_item, remove_table as _remove_table
from .tomlutil import set_value as _set_value, tail_start as _tail_start

_NAME_RE = re.compile(r"[A-Za-z0-9_]+")


class EditError(Exception):
    """A rejected edit; `field` is "<instance>/<key>" as in the API's field errors. The message is in the user's
    language (i18n.t, made when the edit is refused); the field is the code the page acts on."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def parts(doc) -> list:
    """The `part` array of tables (empty when there is none)."""
    if "part" not in doc:
        return []
    return list(doc["part"])


def _find(doc, name: str):
    for p in parts(doc):
        if p.get("name") == name:
            return p
    raise EditError(f"{name}/part", i18n.t("no part named {name}", name=repr(name)))


def _attach_parent(p) -> str | None:
    a = p.get("attach")
    return str(a).split(".", 1)[0] if a is not None else None


def _check_name(doc, name: str, owner: str | None = None) -> None:
    owner = name if owner is None else owner
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise EditError(f"{owner}/name", i18n.t("use letters, digits and underscores only"))
    if any(p.get("name") == name for p in parts(doc)):
        raise EditError(f"{owner}/name", i18n.t("a part named {name} already exists", name=repr(name)))


def _vec(v) -> Any:
    arr = tomlkit.array()
    arr.extend([float(x) for x in v])
    return arr


def _is_zero(v) -> bool:
    return v is None or all(float(x) == 0 for x in v)


def _ids_table(ids: dict):
    t = tomlkit.inline_table()
    for k, v in ids.items():
        t[k] = _plain(v)
    return t


def _is_default(value, default) -> bool:
    try:
        if isinstance(value, (list, tuple)) or isinstance(default, (list, tuple)):
            return list(value) == list(default)
        return value == default
    except Exception:
        return False


def add_part(doc, *, name, stock=None, path=None, attach=None, pos=None, rot=None,
             params=None, ids=None, defaults: dict | None = None) -> None:
    _check_name(doc, name)
    if (stock is None) == (path is None):
        raise EditError(f"{name}/part", i18n.t("give exactly one of stock or path"))
    defaults = defaults or {}
    t = tomlkit.table()
    t["name"] = name
    if stock is not None:
        t["stock"] = stock
    else:
        t["path"] = path
    if attach is not None:
        t["attach"] = attach
    if not _is_zero(pos):
        t["pos"] = _vec(pos)
    if not _is_zero(rot):
        t["rot"] = _vec(rot)
    for k, v in (params or {}).items():
        if v is None or (k in defaults and _is_default(v, defaults[k])):
            continue
        t[k] = _plain(v)
    if ids:
        t["ids"] = _ids_table(ids)
    if "part" in doc:
        arr = doc["part"]
        if len(arr):
            last = arr[-1].value.body
            tail = last[_tail_start(arr[-1]):]
            if not (tail and isinstance(tail[-1][1], Whitespace) and tail[-1][1].s.endswith("\n")):
                last.append((None, Whitespace("\n")))
        arr.append(t)
    else:
        aot = tomlkit.aot()
        aot.append(t)
        doc["part"] = aot


def cad_offset(marker: dict | None, body: str, target_marker: dict | None, chassis_root: bool) -> list[float] | None:
    """The `pos` (m, with `rot` zero) that puts a part built from a CAD import where the CAD has it when it attaches to
    body `body` of another part, or None when that isn't known. `marker` is the part's import-origin.toml
    (library.cad_marker: {import_id, origins}; None for a part not from an import), `target_marker` the target part's.

    Every body frame of an import part is aligned with the robot frame and its root's origin is the robot origin (the
    floor at the frame center), so on the stock chassis's root body (`chassis_root`) the offset is zero. On body B of
    a part from the same import it is -origins[B], B's origin in the robot frame undone: this assumes that part sits
    where the CAD has it. Any other target (another import, a stock part, a mount) has no CAD placement."""
    if marker is None:
        return None
    if chassis_root:
        return [0.0, 0.0, 0.0]
    if target_marker is None or target_marker["import_id"] != marker["import_id"] or body not in target_marker["origins"]:
        return None
    return [0.0 - x for x in target_marker["origins"][body]]     # 0.0 - x: never a -0.0 in the file


def remove_part(doc, name: str) -> None:
    target = _find(doc, name)
    deps = [p["name"] for p in parts(doc) if p is not target and _attach_parent(p) == name]
    if deps:
        raise EditError(f"{name}/part", i18n.t("{name} is attached to by {parts}; move or delete those first",
                                               name=name, parts=", ".join(deps)))
    i = next(k for k, p in enumerate(doc["part"]) if p is target)
    remove_aot_item(doc, "part", i)


def rename_part(doc, old: str, new: str) -> None:
    target = _find(doc, old)
    if new != old:
        _check_name(doc, new, old)
    elif not _NAME_RE.fullmatch(new):
        raise EditError(f"{old}/name", i18n.t("use letters, digits and underscores only"))
    _set_value(target, "name", new)
    for p in parts(doc):
        a = p.get("attach")
        if a is not None and str(a).split(".", 1)[0] == old:
            _set_value(p, "attach", new + str(a)[len(old):])
    # a [[sensor]]'s body names the part too ("<part>.<body>")
    sensors = doc.get("sensor")
    for sn in sensors if isinstance(sensors, list) else ():
        b = sn.get("body") if isinstance(sn, dict) else None
        if isinstance(b, str) and b.split(".", 1)[0] == old and "." in b:
            _set_value(sn, "body", new + b[len(old):])


def set_attach(doc, name: str, attach, pos, rot) -> None:
    p = _find(doc, name)
    if attach is None:
        p.pop("attach", None)
    else:
        _set_value(p, "attach", attach, ("stock", "path", "name"))
    for key, v in (("pos", pos), ("rot", rot)):
        if _is_zero(v):
            p.pop(key, None)
        else:
            after = ("attach", "stock", "path", "name") if key == "pos" else ("pos", "attach", "stock", "path", "name")
            _set_value(p, key, _vec(v), after)


_RESERVED = set(PLACEMENT_KEYS)      # a [[part]]'s keys that aren't a parameter (a path part's grip too)


def set_params(doc, name: str, params: dict, defaults: dict) -> None:
    p = _find(doc, name)
    for k, v in params.items():
        if k in _RESERVED:
            raise EditError(f"{name}/param:{k}", i18n.t("reserved key"))
        if v is None or (k in defaults and _is_default(v, defaults[k])):
            p.pop(k, None)
        else:
            _set_value(p, k, _plain(v))


def set_ids(doc, name: str, ids: dict) -> None:
    p = _find(doc, name)
    if not ids:
        p.pop("ids", None)
        return
    cur = p.get("ids")
    if isinstance(cur, (InlineTable, Table)):   # edit in place: the author's spacing, or the [part.ids] sub-table
        for k in [k for k in cur if k not in ids]:
            del cur[k]
        for k, v in ids.items():
            if k not in cur or cur[k] != v:
                if isinstance(cur, Table):   # a new key goes before the comment block above the next part
                    _set_value(cur, k, _plain(v))
                else:
                    cur[k] = _plain(v)
        return
    _set_value(p, "ids", _ids_table(ids))


# ---- whole-robot sections: the robot code, where it starts and on which field, the battery -----------------------

FIELDS = ("2026", None)     # the start picker's fields: the 2026 game field and the plain field (no [range])
TEST_FIELDS = (None, *BUILTIN_RANGES)   # the Test tab's Setup puts a robot on any built-in field (or the plain one)


def _section(doc, name: str):
    """The top-level table `name`, added at the end of the file when there is none."""
    if name not in doc:
        keys = list(doc.keys())
        if keys and isinstance(doc[keys[-1]], (dict, list)):
            ensure_newline_end(doc[keys[-1]])
        doc[name] = tomlkit.table()
    return doc[name]


def set_program(doc, table: dict) -> None:
    """[robot_program] command and cwd (robotcode.program_table); its other keys (env, start_timeout, ...) stay."""
    sec = _section(doc, "robot_program")
    cmd = tomlkit.array()
    cmd.extend(table["command"])
    _set_value(sec, "command", cmd)
    _set_value(sec, "cwd", table["cwd"], ("command",))


def _number(field: str, v, *, positive: bool) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 or (positive and v == 0):
        raise EditError(field, i18n.t("must be a number above 0, not {value}", value=repr(v)) if positive
                        else i18n.t("must be a number of at least 0, not {value}", value=repr(v)))
    return float(v)


def set_battery(doc, values: dict) -> None:
    """[battery] nominal_voltage (V) and resistance (ohm); a null value removes the key (scrimmage's default)."""
    sec = None
    for key, positive in (("nominal_voltage", True), ("resistance", False)):
        if key not in values:
            continue
        v = values[key]
        if v is None:
            if "battery" in doc:
                doc["battery"].pop(key, None)
            continue
        v = _number(f"battery/{key}", v, positive=positive)
        sec = sec or _section(doc, "battery")
        _set_value(sec, key, v, ("nominal_voltage",))


def _pose(v) -> list[float]:
    if not isinstance(v, (list, tuple)) or len(v) != 3 or not all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in v):
        raise EditError("start/pose", i18n.t("the start must be [x, y, heading]: meters from the blue corner, and degrees"))
    x, y, h = (float(a) for a in v)
    if not (0.0 <= x <= FIELD_LENGTH and 0.0 <= y <= FIELD_WIDTH):
        raise EditError("start/pose", i18n.t("({x}, {y}) is off the field ({length} m by {width} m)", x=f"{x:g}",
                                             y=f"{y:g}", length=f"{FIELD_LENGTH:g}", width=f"{FIELD_WIDTH:g}"))
    h %= 360.0
    if h > 180.0:
        h -= 360.0   # (-180, 180]
    # to 1 µm, not 1 mm: a start typed in feet (11.5 ft = 3.5052 m) reads back as typed
    return [round(x, 6), round(y, 6), round(h, 1) + 0.0]


def _valid_start_pose(doc) -> bool:
    """The config has a [robot] start_pose that set_start would write: 3 numbers on the field."""
    pose = doc.get("robot", {}).get("start_pose")
    if pose is None:
        return False
    try:
        _pose(list(pose) if isinstance(pose, list) else pose)
    except EditError:
        return False
    return True


def set_start(doc, body: dict) -> None:
    """Where the robot starts and on which field, from the keys present in `body`:
    range: a built-in range's name ("2026", "ladder", "station", ...: the Test tab's Setup offers every one) or None
    (the plain field: no [range]); absent keeps the config's field;
    pose: [x, y, heading] writes [robot] start_pose and removes [range] start; None clears the start;
    named: a start of the field's range writes [range] start and removes [robot] start_pose; None clears it;
    keep_pose: true with named keeps a [robot] start_pose that is still valid (on the field) instead, and writes
    no [range] start; without one, named is written as above;
    floor_balls: false writes [range] floor_balls = false, true removes it (the range's default)."""
    if "range" in body:
        rng = body["range"]
        if rng not in FIELDS and rng not in TEST_FIELDS:
            raise EditError("start/range", i18n.t("choose a built-in field or the plain field"))
        if rng is None:
            if "range" in doc:
                _remove_table(doc, "range")
        else:
            sec = _section(doc, "range")
            if sec.get("file") != rng:
                _set_value(sec, "file", rng)
                if "start" in sec and "named" not in body:
                    del sec["start"]   # the other range's named start
    has_range = "range" in doc
    clear = False
    if "pose" in body:
        if body["pose"] is None:
            clear = True
        else:
            pose = _pose(body["pose"])
            if has_range:
                doc["range"].pop("start", None)
            arr = tomlkit.array()
            arr.extend(pose)
            _set_value(_section(doc, "robot"), "start_pose", arr)
    elif "named" in body:
        named = body["named"]
        keep = body.get("keep_pose", False)
        if not isinstance(keep, bool):
            raise EditError("start/keep_pose", i18n.t("keep_pose must be true or false"))
        if keep and named is not None and _valid_start_pose(doc):
            if has_range:
                doc["range"].pop("start", None)
        elif named is None:
            clear = True
        elif not isinstance(named, str) or not named:
            raise EditError("start/named", i18n.t("a named start is the name of one of the field's starts"))
        elif not has_range:
            raise EditError("start/named", i18n.t("the plain field has no named starts; place the robot on it instead"))
        else:
            if "robot" in doc:
                doc["robot"].pop("start_pose", None)
            _set_value(doc["range"], "start", named, ("file",))
    if clear:
        if "robot" in doc:
            doc["robot"].pop("start_pose", None)
        if has_range:
            doc["range"].pop("start", None)
    if "floor_balls" in body and has_range:
        fb = body["floor_balls"]
        if not isinstance(fb, bool):
            raise EditError("start/floor_balls", i18n.t("floor_balls must be true or false"))
        if fb:
            doc["range"].pop("floor_balls", None)
        else:
            _set_value(doc["range"], "floor_balls", False)


# ---- the device check: matching IDs to what the robot code made, and a motor's brake/coast and invert -----------

_SLOT_RE = re.compile(r"(?P<part>[A-Za-z0-9_]+)\.(?P<slot>[A-Za-z0-9_]+)(?:\[(?P<i>\d+)\])?")
# a slot's short form "<Kind>:<id>" -> the device check's label for it (DeviceRow.kind)
_CAN_LABEL = {"TalonFX": "TalonFX", "CANcoder": "CANcoder", "Pigeon2": "Pigeon2", "SparkMax": "SPARK MAX",
              "SparkMaxAbs": "SPARK MAX", "SparkFlex": "SPARK Flex", "SparkFlexAbs": "SPARK Flex"}
_SIM_TYPE = {"SimMotor": "ScrimmageMotor", "SimEncoder": "ScrimmageEncoder", "SimGyro": "ScrimmageGyro"}
_DEVICE_RE = re.compile(r"(?P<label>.+?)(?: |:)(?P<id>\d+)")       # "TalonFX 3", "SPARK MAX 5", "TalonFX:3"
_SCRIMMAGE_RE = re.compile(r"(?P<type>ScrimmageMotor|ScrimmageEncoder|ScrimmageGyro):(?P<name>\S+)")


def _slot_ref(doc, slot, field: str):
    """(container, key, value) of the slot's ID in its part's `ids`: `slot` is the device check's name for it,
    "<part>.<slot>" or "<part>.<slot>[i]" for one motor of a list."""
    m = _SLOT_RE.fullmatch(slot) if isinstance(slot, str) else None
    if m is None:
        raise EditError(field, i18n.t("{slot} isn't a slot: it is <part>.<slot>, e.g. fl.drive", slot=repr(slot)))
    p = next((p for p in parts(doc) if p.get("name") == m["part"]), None)
    ids = p.get("ids") if p is not None else None
    if p is None:
        raise EditError(field, i18n.t("there is no part named {name}", name=repr(m["part"])))
    if not isinstance(ids, dict) or m["slot"] not in ids:
        raise EditError(field, i18n.t("{part} has no device ID for {slot}; give it one in the part inspector",
                                      part=m["part"], slot=repr(m["slot"])))
    container, key = ids, m["slot"]
    value = ids[key]
    if isinstance(value, list):
        if m["i"] is None or int(m["i"]) >= len(value):
            raise EditError(field, i18n.t("{slot} is a list of {count} motors: name one, e.g. {slot}[0]",
                                          slot=f"{m['part']}.{m['slot']}", count=len(value)))
        container, key = value, int(m["i"])
        value = value[key]
    elif m["i"] is not None:
        raise EditError(field, i18n.t("{slot} is one device, not a list", slot=f"{m['part']}.{m['slot']}"))
    return container, key, value


def _device_spec(value) -> tuple[str | None, str]:
    """(the binding key holding the device in a table, or None for the short form; its "<Kind>:<id>" text)."""
    if isinstance(value, str):
        return None, value
    if isinstance(value, dict):
        for k in ("ctre", "rev"):
            if k in value:
                return k, str(value[k])
        if "scrimmage" in value:
            return "scrimmage", str(value["scrimmage"])
    return None, ""


def _matched(slot: str, value, device, field: str):
    """`value` (the slot's ID as written) pointing at `device` instead, the same type."""
    if not isinstance(device, str) or not device.strip():
        raise EditError(field, i18n.t("the device must be one the robot code made, e.g. \"{example}\", not {device}",
                                      device=repr(device), example="TalonFX 3"))
    device = device.strip()
    key, spec = _device_spec(value)
    if isinstance(value, dict) and "device" in value:
        raise EditError(field, i18n.t("{slot} names its device itself ({device_setting}); change it in the part "
                                      "inspector", slot=slot,
                                      device_setting="device = {}".format(repr(value["device"]))))
    kind, _, current = spec.partition(":")
    if key == "scrimmage" or kind in _SIM_TYPE:
        m = _SCRIMMAGE_RE.fullmatch(device)
        if m is None or (kind in _SIM_TYPE and _SIM_TYPE[kind] != m["type"]):
            raise EditError(field, i18n.t("{slot} is a fast-mode {kind}; {device} is not", slot=slot,
                                          kind=_SIM_TYPE.get(kind) or i18n.t("device"), device=device))
        return m["name"] if key == "scrimmage" else f"{kind}:{m['name']}", key
    label = _CAN_LABEL.get(kind)
    if label is None or not current.isdigit():
        raise EditError(field, i18n.t("{slot} isn't a device scrimmage checks ({spec}); set it in the part inspector",
                                      slot=slot, spec=spec or repr(value)))
    m = _DEVICE_RE.fullmatch(device)
    if m is None or m["label"] not in (label, kind):
        raise EditError(field, i18n.t("{slot} is a {label}; {device} is another type of device: check the device type of "
                                      "{slot} in the part inspector", slot=slot, label=label, device=device))
    ident = int(m["id"])
    if not 0 <= ident <= 62:
        raise EditError(field, i18n.t("CAN IDs are 0 to 62, not {id}", id=ident))
    return f"{kind}:{ident}", key


def match_devices(doc, matches) -> None:
    """Point each slot at the device the robot code made: {slot: device} with slot as the device check names it
    ("fl.drive", "fl.drive[1]") and device as its rows do ("TalonFX 3", "SPARK MAX 5", "ScrimmageMotor:fl_drive"; also
    "TalonFX:3"), always the slot's own device type. All of them change in one go, so two slots can swap IDs."""
    if not isinstance(matches, dict) or not matches:
        raise EditError("devices/matches", i18n.t("matches must name at least one slot and the device for it"))
    planned = []
    for slot, device in matches.items():
        field = f"devices/{slot}"
        container, key, value = _slot_ref(doc, slot, field)
        new, table_key = _matched(slot, value, device, field)
        planned.append((container, key, value, new, table_key))
    for container, key, value, new, table_key in planned:   # checked first, then all written: one candidate
        if table_key is None:
            if container[key] != new:
                container[key] = new
        elif value[table_key] != new:
            value[table_key] = new


_MOTOR_KEY = {"TalonFX": "ctre", "SparkMax": "rev", "SparkFlex": "rev"}
_UNSET = object()


def _inline(pairs: list[tuple[str, Any]]):
    """An inline table written as people write them: `{ ctre = "TalonFX:1", neutral = "brake" }`."""
    body = ", ".join(f"{k} = {tomlkit.item(v).as_string()}" for k, v in pairs)
    return tomlkit.parse(f"v = {{ {body} }}\n")["v"]


def _short(table) -> str | None:
    """The short form of a table holding only its device ("TalonFX:1", "SimMotor:fl_drive", "PWM:0"), else None."""
    if len(table) != 1:
        return None
    (k, v), = table.items()
    if k in ("ctre", "rev") and isinstance(v, str):
        return v
    if k == "scrimmage" and isinstance(v, str):
        return f"SimMotor:{v}"
    if k == "pwm" and isinstance(v, int) and not isinstance(v, bool):
        return f"PWM:{v}"
    return None


def set_motor(doc, slot, neutral=_UNSET, invert=_UNSET, motor_slots=None) -> None:
    """A motor slot's neutral mode ("brake", "coast"; None: the device's default) and invert (true flips it from
    the part's own direction; false or None removes it), written into its `ids` entry the way hand-written configs
    do: `drive = { ctre = "TalonFX:1", neutral = "brake" }`. A short ID becomes such a table, and a table left with
    just its device goes back to the short ID. `motor_slots`: the robot's motor names, when known."""
    field = f"motor/{slot}"
    if neutral is not _UNSET and neutral not in ("brake", "coast", None):
        raise EditError(field, i18n.t("neutral must be \"{brake}\" or \"{coast}\", not {value}", value=repr(neutral),
                                      brake="brake", coast="coast"))
    if invert is not _UNSET and invert is not None and not isinstance(invert, bool):
        raise EditError(field, i18n.t("invert must be true or false, not {value}", value=repr(invert)))
    container, key, value = _slot_ref(doc, slot, field)
    if motor_slots is not None and slot not in motor_slots:
        raise EditError(field, i18n.t("{slot} isn't a motor", slot=slot))
    if isinstance(value, str):
        kind, _, n = value.partition(":")
        if kind in _MOTOR_KEY:
            pairs = [(_MOTOR_KEY[kind], value)]
        elif kind == "SimMotor":
            pairs = [("scrimmage", n)]
        elif kind == "PWM" and n.isdigit():
            pairs = [("pwm", int(n))]
        else:
            raise EditError(field, i18n.t("{slot} isn't a motor ({value})", slot=slot, value=value))
        if neutral in (_UNSET, None) and invert in (_UNSET, None, False):
            return                           # nothing to write: the short ID already has the defaults
        if neutral not in (_UNSET, None):
            pairs.append(("neutral", neutral))
        if invert is True:
            pairs.append(("invert", True))
        container[key] = _inline(pairs)
        return
    if not isinstance(value, dict):
        raise EditError(field, i18n.t("{slot} isn't a motor", slot=slot))
    for k, v in (("neutral", neutral), ("invert", invert)):
        if v is _UNSET:
            continue
        if v is None or v is False:
            value.pop(k, None)
        elif value.get(k) != v:
            value[k] = v
    short = _short(value)
    if short is not None and isinstance(value, InlineTable):
        container[key] = short
