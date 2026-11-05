"""A part instance's device slots plus its `ids` table -> binding config dicts (today's [[motor]] etc. keys)."""

from __future__ import annotations

from ..i18n import N_, t, translated
from .model import VALVE_SLOT, Noted, PartError

SHORT = {"TalonFX": ("motor", "ctre"), "CANcoder": ("encoder", "ctre"), "Pigeon2": ("gyro", "ctre"),
         "PWM": ("motor", "pwm"), "DIO": ("encoder", "channel_a"),
         "SparkMax": ("motor", "rev"), "SparkFlex": ("motor", "rev"),
         "SparkMaxAbs": ("encoder", "rev"), "SparkFlexAbs": ("encoder", "rev"),
         "PCM": ("valve", "pcm"), "PH": ("valve", "ph")}
# fast-mode devices take a name, not a number: "SimMotor:fl_drive" -> { scrimmage = "fl_drive" }
SIM_SHORT = {"SimMotor": "motor", "SimEncoder": "encoder", "SimGyro": "gyro"}
PART_KEYS = ("joint", "body", "motor", "count", "gear_ratio", "ratio", "name", "efficiency")
# A valve's module: its kind as in a binding, the default module number, and how many solenoid channels it has
VALVE_MODULES = {"pcm": ("PCM", 0, 8), "ph": ("PH", 1, 16)}
MAX_MODULE = 62         # CAN IDs run 0 to 62


# A slot's kind and a device's kind in a sentence, with their articles (N_(): said with translated())
SLOT_KINDS = {"motor": N_("a motor slot"), "encoder": N_("an encoder slot"), "gyro": N_("a gyro slot"),
              "valve": N_("a valve slot")}
DEVICE_KINDS = {"motor": N_("a motor"), "encoder": N_("an encoder"), "gyro": N_("a gyro"), "valve": N_("a valve")}


def _kind_error(where, slot: str, kind: str, value, device: str) -> PartError:
    """A slot given the wrong kind of device: "ids: slot 'drive' is a motor slot; CANcoder:3 is an encoder"."""
    return PartError(t("{where}: ids: slot {slot} is {slot_kind}; {value} is {device}", where=where, slot=repr(slot),
                       slot_kind=translated(SLOT_KINDS[kind]), value=value, device=translated(DEVICE_KINDS[device])),
                     where=where, field=f"ids:{slot}")


def _ids_error(where, slot: str, message: str) -> PartError:
    return PartError(message, where=where, field=f"ids:{slot}")


def _device(kind: str, slot: str, value, where: str) -> dict:
    if isinstance(value, str):
        k, sep, n = value.partition(":")
        if k in SIM_SHORT:
            if not n:
                raise _ids_error(where, slot, t("{where}: ids: {slot} = {value}: {kind} needs a name, e.g. "
                                                "\"{kind_fl_drive}\"", where=where, slot=slot, value=repr(value),
                                                kind=k, kind_fl_drive="{}:fl_drive".format(k)))
            if any(c.isspace() for c in n):
                raise _ids_error(where, slot, t("{where}: ids: {slot} = {value}: the name takes no spaces",
                                                where=where, slot=slot, value=repr(value)))
            if SIM_SHORT[k] != kind:
                raise _kind_error(where, slot, kind, value, SIM_SHORT[k])
            return {"scrimmage": n}
        if k in SHORT and SHORT[k][0] == "valve":
            if kind != "valve":
                raise _kind_error(where, slot, kind, value, "valve")
            channels = n.split(",")
            if not sep or len(channels) > 2 or not all(x.isdigit() for x in channels):
                raise _ids_error(where, slot, t("{where}: ids: {slot} = {value}: expected \"{kind_3}\" (a single "
                                                "solenoid) or \"{kind_3_4}\" (a double solenoid's forward and "
                                                "reverse channels)", where=where, slot=slot, value=repr(value),
                                                kind_3="{}:3".format(k), kind_3_4="{}:3,4".format(k)))
            module = SHORT[k][1]
            ch = [int(x) for x in channels]
            return _valve(module, VALVE_MODULES[module][1], ch[0], ch[1] if len(ch) == 2 else None, False,
                          slot, where)
        if kind == "valve" and k not in SHORT:
            raise _ids_error(where, slot, t("{where}: ids: {slot} = {value}: a valve takes \"{pcm_channel}\" or "
                                            "\"{ph_channel}\" (\"{ph_3_4}\" for a double solenoid), or a table like "
                                            "{pcm_1_form}", where=where, slot=slot, value=repr(value),
                                            pcm_channel="PCM:<channel>", ph_channel="PH:<channel>", ph_3_4="PH:3,4",
                                            pcm_1_form="{ pcm = 1, channel = 3 }"))
        if not sep or not n.isdigit():
            raise _ids_error(where, slot, t("{where}: ids: {slot} = {value}: expected \"{kind_number}\", e.g. "
                                            "\"{talonfx_3}\"", where=where, slot=slot, value=repr(value),
                                            kind_number="<Kind>:<number>", talonfx_3="TalonFX:3"))
        if k not in SHORT:
            raise _ids_error(where, slot, t("{where}: ids: {slot} = {value}: Kind one of {kinds} (other devices take "
                                            "a table of binding keys, e.g. {can_victorspx_form})", where=where,
                                            slot=slot, value=repr(value), kinds=", ".join([*SHORT, *SIM_SHORT]),
                                            can_victorspx_form="{ can = \"VictorSPX[3]\" }"))
        want, key = SHORT[k]
        if want != kind:
            raise _kind_error(where, slot, kind, value, want)
        return {key: value if key in ("ctre", "rev") else int(n)}
    if isinstance(value, dict) and kind == "valve":
        return _valve_table(slot, value, where)
    if isinstance(value, dict):
        for k in value:
            if k in PART_KEYS:
                raise _ids_error(where, slot, t("{where}: ids: {slot}: {key} comes from the part; leave it out",
                                                where=where, slot=slot, key=repr(k)))
        return dict(value)
    if kind == "motor":
        raise _ids_error(where, slot, t("{where}: ids: {slot} must be \"{kind_number}\", a table of binding keys or "
                                        "a list of them, not {value}", where=where, slot=slot, value=repr(value),
                                        kind_number="<Kind>:<number>"))
    raise _ids_error(where, slot, t("{where}: ids: {slot} must be \"{kind_number}\", a table of binding keys, not "
                                    "{value}", where=where, slot=slot, value=repr(value),
                                    kind_number="<Kind>:<number>"))


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _valve(module: str, number, forward, reverse, invert: bool, slot: str, where: str) -> dict:
    """A valve binding's device keys, checked: module "pcm" or "ph", its CAN number, and the channels."""
    kind, _, channels = VALVE_MODULES[module]
    if not _whole(number) or not 0 <= number <= MAX_MODULE:
        raise _ids_error(where, slot, t("{where}: ids: {slot}: the {module}'s number must be a whole number from 0 to "
                                        "{most}, not {number}", where=where, slot=slot, module=kind, most=MAX_MODULE,
                                        number=repr(number)))
    for ch in (forward, reverse):
        if ch is not None and (not _whole(ch) or not 0 <= ch < channels):
            raise _ids_error(where, slot, t("{where}: ids: {slot}: channel {channel} isn't on a {module}: its "
                                            "channels are 0 to {last}", where=where, slot=slot, channel=repr(ch),
                                            module=kind, last=channels - 1))
    if forward == reverse:
        raise _ids_error(where, slot, t("{where}: ids: {slot}: a double solenoid's forward and reverse need different "
                                        "channels, not both {channel}", where=where, slot=slot, channel=forward))
    return {"module": kind, "id": number, "forward": forward, "reverse": reverse, "invert": invert}


def _valve_table(slot: str, value: dict, where: str) -> dict:
    """{ pcm = 1, channel = 3 } or { ph = 2, forward = 3, reverse = 4 }, with an optional invert."""
    for k in value:
        if k not in ("pcm", "ph", "channel", "forward", "reverse", "invert"):
            raise _ids_error(where, slot, t("{where}: ids: {slot}: unknown key {key}; a valve takes pcm or ph, "
                                            "channel (or forward and reverse), and invert", where=where, slot=slot,
                                            key=repr(k)))
    modules = [k for k in VALVE_MODULES if k in value]
    if len(modules) != 1:
        raise _ids_error(where, slot, t("{where}: ids: {slot}: give exactly one of {pcm_setting} or {ph_setting}",
                                        where=where, slot=slot, pcm_setting="pcm = <number>",
                                        ph_setting="ph = <number>"))
    if ("channel" in value) == ("forward" in value) or ("reverse" in value and "forward" not in value):
        raise _ids_error(where, slot, t("{where}: ids: {slot}: give {channel_setting} (a single solenoid) or "
                                        "{forward_setting}, {reverse_setting} (a double solenoid)", where=where,
                                        slot=slot, channel_setting="channel = <n>", forward_setting="forward = <n>",
                                        reverse_setting="reverse = <n>"))
    invert = value.get("invert", False)
    if not isinstance(invert, bool):
        raise _ids_error(where, slot, t("{where}: ids: {slot}: invert must be true or false, not {invert}",
                                        where=where, slot=slot, invert=repr(invert)))
    module = modules[0]
    return _valve(module, value[module], value.get("channel", value.get("forward")), value.get("reverse"), invert,
                  slot, where)


def slot_bindings(inst) -> tuple[dict[str, list[dict]], list[str]]:
    part, n, where = inst.part, inst.name, inst.where
    kinds = part.slot_kinds()
    for slot in inst.ids:
        if slot not in kinds:
            if kinds:
                raise _ids_error(where, slot, t("{where}: ids: no slot {slot}; the part's slots: {slots}",
                                                where=where, slot=repr(slot), slots=", ".join(kinds)))
            raise _ids_error(where, slot, t("{where}: ids: no slot {slot}; the part's slots: none", where=where,
                                            slot=repr(slot)))
    out: dict[str, list[dict]] = {"motor": [], "encoder": [], "gyro": [], "valve": []}
    warnings: list[str] = []

    def missing(slot: str, said: str) -> None:
        warnings.append(Noted(said, ((n, f"ids:{slot}"),)))

    for s in part.motors:
        value = inst.ids.get(s.slot)
        if value is None:
            missing(s.slot, t("{part}.{slot} has no device ID, so it won't move", part=n, slot=s.slot))
            continue
        values = value if isinstance(value, list) else [value]
        if isinstance(value, list) and len(value) != s.count:
            raise _ids_error(where, s.slot, t("{where}: ids: slot {slot} has {count} motors: give one ID or a list of "
                                              "{count}", where=where, slot=repr(s.slot), count=s.count))
        for i, v in enumerate(values):
            cfg = _device("motor", s.slot, v, where)
            cfg.update(name=f"{n}.{s.slot}" + (f"[{i}]" if isinstance(value, list) else ""),
                       joint=f"{n}.{s.joint}", motor=s.motor, count=1 if isinstance(value, list) else s.count,
                       gear_ratio=s.gearing, efficiency=s.efficiency,
                       invert=s.invert != bool(cfg.get("invert", False)))
            out["motor"].append(cfg)
    for s in part.encoders:
        value = inst.ids.get(s.slot)
        if value is None:
            missing(s.slot, t("{part}.{slot} has no device ID, so it won't report", part=n, slot=s.slot))
            continue
        cfg = _device("encoder", s.slot, value, where)
        cfg.update(name=f"{n}.{s.slot}", joint=f"{n}.{s.joint}", ratio=s.ratio,
                   invert=s.invert != bool(cfg.get("invert", False)))
        out["encoder"].append(cfg)
    for s in part.gyros:
        value = inst.ids.get(s.slot)
        if value is None:
            missing(s.slot, t("{part}.{slot} has no device ID, so it won't report", part=n, slot=s.slot))
            continue
        cfg = _device("gyro", s.slot, value, where)
        cfg.update(name=f"{n}.{s.slot}", body=f"{n}.{s.body}")
        out["gyro"].append(cfg)
    if part.cylinders:
        value = inst.ids.get(VALVE_SLOT)
        if value is None:
            missing(VALVE_SLOT, t("{part}.{slot} has no device ID, so robot code can't move it (the Test tab's "
                                  "blocks still can)", part=n, slot=VALVE_SLOT))
        else:
            out["valve"].append({"name": f"{n}.{VALVE_SLOT}", "cylinder": n,
                                 **_device("valve", VALVE_SLOT, value, where)})
    return out, warnings
