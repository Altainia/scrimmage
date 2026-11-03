"""A part instance's device slots plus its `ids` table -> binding config dicts (today's [[motor]] etc. keys)."""

from __future__ import annotations

from .model import VALVE_SLOT, PartError

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


def _a(word: str) -> str:
    return f"{'an' if word[:1] in 'aeiou' else 'a'} {word}"


def _device(kind: str, slot: str, value, where: str) -> dict:
    if isinstance(value, str):
        k, sep, n = value.partition(":")
        if k in SIM_SHORT:
            if not n:
                raise PartError(f"{where}: ids: {slot} = {value!r}: {k} needs a name, e.g. \"{k}:fl_drive\"")
            if any(c.isspace() for c in n):
                raise PartError(f"{where}: ids: {slot} = {value!r}: the name takes no spaces")
            if SIM_SHORT[k] != kind:
                raise PartError(f"{where}: ids: slot {slot!r} is {_a(kind)} slot; {value} is {_a(SIM_SHORT[k])}")
            return {"scrimmage": n}
        if k in SHORT and SHORT[k][0] == "valve":
            if kind != "valve":
                raise PartError(f"{where}: ids: slot {slot!r} is {_a(kind)} slot; {value} is a valve")
            channels = n.split(",")
            if not sep or len(channels) > 2 or not all(x.isdigit() for x in channels):
                raise PartError(f"{where}: ids: {slot} = {value!r}: expected \"{k}:3\" (a single solenoid) or "
                                f"\"{k}:3,4\" (a double solenoid's forward and reverse channels)")
            module = SHORT[k][1]
            ch = [int(x) for x in channels]
            return _valve(module, VALVE_MODULES[module][1], ch[0], ch[1] if len(ch) == 2 else None, False,
                          slot, where)
        if kind == "valve" and k not in SHORT:
            raise PartError(f"{where}: ids: {slot} = {value!r}: a valve takes \"PCM:<channel>\" or "
                            "\"PH:<channel>\" (\"PH:3,4\" for a double solenoid), or a table like "
                            "{ pcm = 1, channel = 3 }")
        if not sep or not n.isdigit():
            raise PartError(f"{where}: ids: {slot} = {value!r}: expected \"<Kind>:<number>\", e.g. \"TalonFX:3\"")
        if k not in SHORT:
            raise PartError(f"{where}: ids: {slot} = {value!r}: Kind one of {', '.join([*SHORT, *SIM_SHORT])} "
                            "(other devices take a table of binding keys, e.g. { can = \"VictorSPX[3]\" })")
        want, key = SHORT[k]
        if want != kind:
            raise PartError(f"{where}: ids: slot {slot!r} is {_a(kind)} slot; {value} is {_a(want)}")
        return {key: value if key in ("ctre", "rev") else int(n)}
    if isinstance(value, dict) and kind == "valve":
        return _valve_table(slot, value, where)
    if isinstance(value, dict):
        for k in value:
            if k in PART_KEYS:
                raise PartError(f"{where}: ids: {slot}: {k!r} comes from the part; leave it out")
        return dict(value)
    raise PartError(f"{where}: ids: {slot} must be \"<Kind>:<number>\", a table of binding keys"
                    f"{' or a list of them' if kind == 'motor' else ''}, not {value!r}")


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _valve(module: str, number, forward, reverse, invert: bool, slot: str, where: str) -> dict:
    """A valve binding's device keys, checked: module "pcm" or "ph", its CAN number, and the channels."""
    kind, _, channels = VALVE_MODULES[module]
    pre = f"{where}: ids: {slot}"
    if not _whole(number) or not 0 <= number <= MAX_MODULE:
        raise PartError(f"{pre}: the {kind}'s number must be a whole number from 0 to {MAX_MODULE}, not {number!r}")
    for ch in (forward, reverse):
        if ch is not None and (not _whole(ch) or not 0 <= ch < channels):
            raise PartError(f"{pre}: channel {ch!r} isn't on a {kind}: its channels are 0 to {channels - 1}")
    if forward == reverse:
        raise PartError(f"{pre}: a double solenoid's forward and reverse need different channels, not both {forward}")
    return {"module": kind, "id": number, "forward": forward, "reverse": reverse, "invert": invert}


def _valve_table(slot: str, value: dict, where: str) -> dict:
    """{ pcm = 1, channel = 3 } or { ph = 2, forward = 3, reverse = 4 }, with an optional invert."""
    pre = f"{where}: ids: {slot}"
    for k in value:
        if k not in ("pcm", "ph", "channel", "forward", "reverse", "invert"):
            raise PartError(f"{pre}: unknown key {k!r}; a valve takes pcm or ph, channel (or forward and reverse), "
                            "and invert")
    modules = [k for k in VALVE_MODULES if k in value]
    if len(modules) != 1:
        raise PartError(f"{pre}: give exactly one of pcm = <number> or ph = <number>")
    if ("channel" in value) == ("forward" in value) or ("reverse" in value and "forward" not in value):
        raise PartError(f"{pre}: give channel = <n> (a single solenoid) or forward = <n>, reverse = <n> "
                        "(a double solenoid)")
    invert = value.get("invert", False)
    if not isinstance(invert, bool):
        raise PartError(f"{pre}: invert must be true or false, not {invert!r}")
    module = modules[0]
    return _valve(module, value[module], value.get("channel", value.get("forward")), value.get("reverse"), invert,
                  slot, where)


def slot_bindings(inst) -> tuple[dict[str, list[dict]], list[str]]:
    part, n, where = inst.part, inst.name, inst.where
    kinds = part.slot_kinds()
    for slot in inst.ids:
        if slot not in kinds:
            raise PartError(f"{where}: ids: no slot {slot!r}; the part's slots: {', '.join(kinds) or 'none'}")
    out: dict[str, list[dict]] = {"motor": [], "encoder": [], "gyro": [], "valve": []}
    warnings: list[str] = []

    def missing(slot: str, consequence: str) -> None:
        warnings.append(f"{n}.{slot} has no device ID, so {consequence}")

    for s in part.motors:
        value = inst.ids.get(s.slot)
        if value is None:
            missing(s.slot, "it won't move")
            continue
        values = value if isinstance(value, list) else [value]
        if isinstance(value, list) and len(value) != s.count:
            raise PartError(f"{where}: ids: slot {s.slot!r} has {s.count} motors: give one ID or a list of {s.count}")
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
            missing(s.slot, "it won't report")
            continue
        cfg = _device("encoder", s.slot, value, where)
        cfg.update(name=f"{n}.{s.slot}", joint=f"{n}.{s.joint}", ratio=s.ratio,
                   invert=s.invert != bool(cfg.get("invert", False)))
        out["encoder"].append(cfg)
    for s in part.gyros:
        value = inst.ids.get(s.slot)
        if value is None:
            missing(s.slot, "it won't report")
            continue
        cfg = _device("gyro", s.slot, value, where)
        cfg.update(name=f"{n}.{s.slot}", body=f"{n}.{s.body}")
        out["gyro"].append(cfg)
    if part.cylinders:
        value = inst.ids.get(VALVE_SLOT)
        if value is None:
            missing(VALVE_SLOT, "robot code can't move it (the Test tab's blocks still can)")
        else:
            out["valve"].append({"name": f"{n}.{VALVE_SLOT}", "cylinder": n,
                                 **_device("valve", VALVE_SLOT, value, where)})
    return out, warnings
