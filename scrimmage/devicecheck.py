"""Which devices the config expects, beside which the robot code made, in plain words.

One structured list (`check_devices`, for the editor page) and the plain lines that go with it (`device_notes`, for
scenario reports and the session status). The matching is the bridge's: `resolve_device_key` for the lookup, the
bridge's name patterns for reading ids out of device names."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from . import i18n
from .bindings import CTRE_DEVICES, resolve_device_key
from .bridge import (MISSING_AFTER, PHOENIX_NAME, REV_LABEL, REV_NAME, REV_OWN_NAME, REV_TYPE, SCRIMMAGE_TYPES,
                     rev_unhelped)
from .protocol import HalState

Status = Literal["ok", "waiting", "missing", "extra", "other_type", "not_checked"]
_PHOENIX_LABEL = {v: k for k, v in CTRE_DEVICES.items()}      # "Talon FX (v6)" -> "TalonFX"


@dataclass
class DeviceRow:
    slot: str | None            # the binding's name ("fl.drive"); None for a device nothing binds
    kind: str                   # "TalonFX", "CANcoder", "Pigeon2", "SPARK MAX", "Scrimmage motor", ...
    expected: str | None        # "TalonFX 3"
    made: str | None            # what the robot code made: the same device, or (other_type / extra) the one it made
    status: Status
    suggest: list[str] = field(default_factory=list)   # same-type devices the code made that nothing binds
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _ident(label: str, ident: int) -> str:
    return f"{label} {ident}"


def _binding_name(b, kind: str, index: int) -> str:
    return getattr(b, "name", None) or f"{kind} #{index}"


def _made_devices(hal: HalState) -> dict[tuple[str, int], set[str]]:
    """The CAN devices the robot code made, (label, id) -> where they were seen ("phoenix", "helper", "own")."""
    made: dict[tuple[str, int], set[str]] = {}
    for typ, name in hal.devices:
        if m := PHOENIX_NAME.search(name):
            made.setdefault((_PHOENIX_LABEL[m["kind"]], int(m["id"])), set()).add("phoenix")
        elif typ == REV_TYPE and (m := REV_NAME.fullmatch(name)):
            made.setdefault((REV_LABEL[m["kind"]], int(m["id"])), set()).add("helper")
        elif m := REV_OWN_NAME.fullmatch(name):
            made.setdefault((m["label"], int(m["id"])), set()).add("own")
    return made


def _expected_can(key: tuple[str, str]) -> tuple[str, int] | None:
    typ, name = key
    if m := PHOENIX_NAME.search(name):
        return _PHOENIX_LABEL[m["kind"]], int(m["id"])
    if typ == REV_TYPE and (m := REV_NAME.fullmatch(name)):
        return REV_LABEL[m["kind"]], int(m["id"])
    return None


def check_devices(world, hal: HalState, robot_time: float) -> list[DeviceRow]:
    """One row per device binding (Phoenix 6, REV SPARK, fast-mode), then a row for each device the robot code made
    that nothing binds. `robot_time`: simulated seconds since the robot connected (the world's clock, which restarts
    at 0 on every connection, as the bridge's own check uses it: `Session.t`); before MISSING_AFTER a device the
    code hasn't made yet is "waiting", not "missing"."""
    bindings = ([(b, "motor") for b in world.motors] + [(b, "encoder") for b in world.encoders]
                + [(b, "gyro") for b in world.gyros])
    made = _made_devices(hal)
    bound_can = {e for b, _ in bindings if (e := _expected_can(b.device_key))}
    bound_keys = {b.device_key for b, _ in bindings}
    counters = {"motor": 0, "encoder": 0, "gyro": 0}
    rows: list[DeviceRow] = []
    for b, what in bindings:
        counters[what] += 1
        slot = _binding_name(b, what, counters[what])
        key = b.device_key
        typ, name = key
        can = _expected_can(key)
        if can is not None:
            rows.append(_can_row(slot, can, key, hal, made, bound_can, robot_time))
        elif typ in SCRIMMAGE_TYPES:
            rows.append(_scrimmage_row(slot, key, hal, bound_keys, robot_time))
        else:
            rows.append(DeviceRow(slot, _plain_kind(b, what), None, None, "not_checked", [],
                                  i18n.t("{slot} is not checked: scrimmage only checks Phoenix 6, REV SPARK and "
                                         "fast-mode devices", slot=slot)))
    for (label, ident), _ in sorted(made.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        if (label, ident) not in bound_can:
            rows.append(DeviceRow(None, label, None, _ident(label, ident), "extra", [],
                                  i18n.t("the robot code made {device}, but nothing in the config uses it",
                                         device=_ident(label, ident))))
    for typ, name in sorted(d for d in hal.devices if d[0] in SCRIMMAGE_TYPES):
        if (typ, name) not in bound_keys:
            rows.append(DeviceRow(None, _scrimmage_kind(typ), None, f"{typ}:{name}", "extra", [],
                                  i18n.t("the robot code made {device}, but nothing in the config uses it",
                                         device=f"{typ}:{name}")))
    return rows


def _plain_kind(b, what: str) -> str:
    return {"PWM": "PWM motor", "CANMotor": "CAN motor", "CANEncoder": "CAN encoder", "Encoder": "encoder",
            }.get(getattr(b, "kind", ""), what)


def _scrimmage_kind(typ: str) -> str:
    return "Scrimmage " + typ.removeprefix("Scrimmage").lower()


def _can_row(slot, can, key, hal, made, bound_can, robot_time) -> DeviceRow:
    label, ident = can
    want = _ident(label, ident)
    if resolve_device_key(hal, key) is not None:
        return DeviceRow(slot, label, want, want, "ok", [],
                         i18n.t("{slot}: {device} is there", slot=slot, device=want))
    sources = made.get(can)
    if sources and label in REV_LABEL.values() and rev_unhelped(hal, [key]):
        return DeviceRow(slot, label, want, None, "missing", [],
                         i18n.t("{slot} expects {device}: the robot code made it, but scrimmage's REV helper is not "
                                "driving it, so it runs with no physics (the scrimmage add-on for REV motor "
                                "controllers may not be loaded in the robot program)", slot=slot, device=want))
    if sources:   # same device under another name
        return DeviceRow(slot, label, want, want, "missing", [],
                         i18n.t("{slot} expects {device}, but the robot code made it under a different device name: "
                                "the robot file needs to use the name the robot code gave it", slot=slot, device=want))
    if robot_time < MISSING_AFTER - 1e-6:
        return DeviceRow(slot, label, want, None, "waiting", [],
                         i18n.t("{slot} expects {device}; the robot code has not made it yet", slot=slot, device=want))
    others = sorted(lb for (lb, i) in made if i == ident and lb != label and (lb, i) not in bound_can)
    if others:
        other = _ident(others[0], ident)
        return DeviceRow(slot, label, want, other, "other_type", [],
                         i18n.t("{slot} expects {device}, but the robot code made {other} with that ID: check the "
                                "device type of {slot} in the part inspector", slot=slot, device=want, other=other))
    near = sorted(((abs(i - ident), i) for (lb, i) in made if lb == label and (lb, i) not in bound_can))
    suggest = [_ident(label, i) for _, i in near]
    if suggest:
        note = i18n.t("{slot} expects {device}, but the robot code made no {device}; it made {made}, which nothing "
                      "uses", slot=slot, device=want, made=_join(suggest))
    elif not any(lb == label for lb, _ in made):
        note = i18n.t("{slot} expects {device}, but the robot code made no {device}; it made no {kind} at all",
                      slot=slot, device=want, kind=label)
    else:
        note = i18n.t("{slot} expects {device}, but the robot code made no {device}", slot=slot, device=want)
    return DeviceRow(slot, label, want, None, "missing", suggest, note)


def _scrimmage_row(slot, key, hal, bound_keys, robot_time) -> DeviceRow:
    typ, name = key
    want, kind = f"{typ}:{name}", _scrimmage_kind(typ)
    if key in hal.devices:
        return DeviceRow(slot, kind, want, want, "ok", [], i18n.t("{slot}: {device} is there", slot=slot, device=want))
    if robot_time < MISSING_AFTER - 1e-6:
        return DeviceRow(slot, kind, want, None, "waiting", [],
                         i18n.t("{slot} expects {device}; the robot code has not made it yet", slot=slot, device=want))
    suggest = sorted(f"{t}:{d}" for t, d in hal.devices if t == typ and (t, d) not in bound_keys)
    if suggest:
        note = i18n.t("{slot} expects {device}, but the robot code never made it (the fast simulation devices); it "
                      "made {made}, which nothing uses", slot=slot, device=want, made=_join(suggest))
    else:
        note = i18n.t("{slot} expects {device}, but the robot code never made it (the fast simulation devices)",
                      slot=slot, device=want)
    return DeviceRow(slot, kind, want, None, "missing", suggest, note)


def _join(names: list[str], most: int = 3) -> str:
    if len(names) <= most:
        return ", ".join(names)
    return i18n.t("{names} and {count} more", names=", ".join(names[:most]), count=len(names) - most)


def device_notes(rows: list[DeviceRow], final: bool = False) -> list[str]:
    """Plain lines about what is wrong (nothing for devices that match or aren't checked). An extra device that a
    missing one already names is not repeated. `final`: the run is over, so a device still "waiting" was never made
    in time and is reported. When the code made none of the expected devices, one line says so."""
    named = {s for r in rows for s in r.suggest}
    checked = [r for r in rows if r.expected is not None]
    if checked and all(r.status == "missing" and not r.suggest for r in checked) \
            and not any(r.status in ("extra", "other_type") for r in rows):
        names = _join([r.expected for r in checked], most=6)
        return [i18n.t("the robot code made none of the devices the robot file expects ({devices})", devices=names)]
    out = []
    for r in rows:
        if r.status == "waiting" and final:
            out.append(i18n.t("{slot}'s {device} wasn't made yet when the run ended", slot=r.slot, device=r.expected))
        elif r.status in ("missing", "other_type"):
            out.append(r.note)
        elif r.status == "extra" and r.made not in named:
            out.append(r.note)
    return out
