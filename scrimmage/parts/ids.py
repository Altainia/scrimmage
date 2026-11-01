"""A part instance's device slots plus its `ids` table -> binding config dicts (today's [[motor]] etc. keys)."""

from __future__ import annotations

from .model import PartError

SHORT = {"TalonFX": ("motor", "ctre"), "CANcoder": ("encoder", "ctre"), "Pigeon2": ("gyro", "ctre"),
         "PWM": ("motor", "pwm"), "DIO": ("encoder", "channel_a"),
         "SparkMax": ("motor", "rev"), "SparkFlex": ("motor", "rev"),
         "SparkMaxAbs": ("encoder", "rev"), "SparkFlexAbs": ("encoder", "rev")}
# fast-mode devices take a name, not a number: "SimMotor:fl_drive" -> { scrimmage = "fl_drive" }
SIM_SHORT = {"SimMotor": "motor", "SimEncoder": "encoder", "SimGyro": "gyro"}
PART_KEYS = ("joint", "body", "motor", "count", "gear_ratio", "ratio", "name", "efficiency")


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
        if not sep or not n.isdigit():
            raise PartError(f"{where}: ids: {slot} = {value!r}: expected \"<Kind>:<number>\", e.g. \"TalonFX:3\"")
        if k not in SHORT:
            raise PartError(f"{where}: ids: {slot} = {value!r}: Kind one of {', '.join([*SHORT, *SIM_SHORT])} "
                            "(other devices take a table of binding keys, e.g. { can = \"VictorSPX[3]\" })")
        want, key = SHORT[k]
        if want != kind:
            raise PartError(f"{where}: ids: slot {slot!r} is {_a(kind)} slot; {value} is {_a(want)}")
        return {key: value if key in ("ctre", "rev") else int(n)}
    if isinstance(value, dict):
        for k in value:
            if k in PART_KEYS:
                raise PartError(f"{where}: ids: {slot}: {k!r} comes from the part; leave it out")
        return dict(value)
    raise PartError(f"{where}: ids: {slot} must be \"<Kind>:<number>\", a table of binding keys"
                    f"{' or a list of them' if kind == 'motor' else ''}, not {value!r}")


def slot_bindings(inst) -> tuple[dict[str, list[dict]], list[str]]:
    part, n, where = inst.part, inst.name, inst.where
    kinds = part.slot_kinds()
    for slot in inst.ids:
        if slot not in kinds:
            raise PartError(f"{where}: ids: no slot {slot!r}; the part's slots: {', '.join(kinds) or 'none'}")
    out: dict[str, list[dict]] = {"motor": [], "encoder": [], "gyro": []}
    warnings: list[str] = []

    def missing(slot: str, verb: str) -> None:
        warnings.append(f"{n}.{slot} has no device ID, so it won't {verb}")

    for s in part.motors:
        value = inst.ids.get(s.slot)
        if value is None:
            missing(s.slot, "move")
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
            missing(s.slot, "report")
            continue
        cfg = _device("encoder", s.slot, value, where)
        cfg.update(name=f"{n}.{s.slot}", joint=f"{n}.{s.joint}", ratio=s.ratio,
                   invert=s.invert != bool(cfg.get("invert", False)))
        out["encoder"].append(cfg)
    for s in part.gyros:
        value = inst.ids.get(s.slot)
        if value is None:
            missing(s.slot, "report")
            continue
        cfg = _device("gyro", s.slot, value, where)
        cfg.update(name=f"{n}.{s.slot}", body=f"{n}.{s.body}")
        out["gyro"].append(cfg)
    return out, warnings
