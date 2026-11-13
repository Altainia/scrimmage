"""The bench file's shafts and shapes: [shaft.<name>], [[wheel]], [[plate]], [[bar]], [[curve]], [[cad]], [[inlet]],
[[outlet]], [flight] (with a goal from a range file) and [run], each parsed into spec.py's dataclasses.
"""

from pathlib import Path

from .. import i18n
from ..launcher.inputs import PRESETS
from ..motors import MOTORS
from ..testrange.spec import BUILTIN_RANGES, RangeError, load_range
from .spec import (THICKNESS_DEFAULT, _REQ, Bar, Cad, Curve, Flight, FlightGoal, Inlet, Outlet, Plate, RunSettings,
                   Shaft, Wheel, _Table, _material, _resolve_path, _unit_vec, _units, _valid_keys)


def _parse_shaft(name: str, raw) -> Shaft:
    t = _Table("shaft", f"[shaft.{name}]", raw, name)
    motor = t.text("motor", None)
    if motor is not None and motor not in MOTORS:
        raise t.err(i18n.t("{table} motor {motor} is not known; known motors: {known}", table=t.label,
                           motor=repr(motor), known=", ".join(sorted(MOTORS))), "motor")
    target = t.num("target_rps", None, positive=True)
    voltage = t.num("voltage", None)
    coupled = t.text("coupled_to", None)
    if target is not None and voltage is not None:
        raise t.err(i18n.t("{table} has both target_rps and voltage; give one", table=t.label), "voltage")
    if "control" in t.t:
        control = t.text("control", one_of=("speed", "voltage", "free"))
    elif target is not None:
        control = "speed"
    elif voltage is not None:
        control = "voltage"
    elif motor is None or coupled is not None:
        control = "free"
    else:
        raise t.err(i18n.t("{table} has motor {motor} but neither target_rps nor voltage; give one, or "
                           "{control_setting}", table=t.label, motor=repr(motor),
                           control_setting="control = \"free\""))
    if control == "speed" and target is None:
        raise t.err(i18n.t("{table} control is speed, so it needs target_rps", table=t.label), "target_rps")
    if control == "voltage" and voltage is None:
        raise t.err(i18n.t("{table} control is voltage, so it needs voltage", table=t.label), "voltage")
    if control == "free" and (target is not None or voltage is not None):
        raise t.err(i18n.t("{table} control is free, so it takes no target_rps or voltage", table=t.label))
    if control != "free" and motor is None:
        raise t.err(i18n.t("{table} has a target_rps or voltage but no motor; a motor is needed to drive the shaft",
                           table=t.label))
    if control == "speed" and "kP" not in t.t:
        raise t.err(i18n.t("{table} control is speed, so it needs kP (volts per rps of error); valid keys: {keys}",
                           table=t.label, keys=", ".join(_valid_keys("shaft"))))
    if coupled is not None and (motor is not None or control != "free"):
        raise t.err(i18n.t("{table} is coupled to {shaft}, so it takes no motor or control (the coupling drives it)",
                           table=t.label, shaft=repr(coupled)))
    if "ratio" in t.t and coupled is None:
        raise t.err(i18n.t("{table} has a ratio but no coupled_to", table=t.label), "ratio")
    wheel_inertia = t.num("wheel_inertia", nonneg=True)
    added = t.num("added_inertia", 0.0, nonneg=True)
    if wheel_inertia + added <= 0.0:
        raise t.err(i18n.t("{table} wheel_inertia + added_inertia must be positive", table=t.label))
    ratio = t.num("ratio", 1.0)
    if ratio == 0.0:
        raise t.err(i18n.t("{table} ratio must not be zero", table=t.label), "ratio")
    return Shaft(
        name=name, motor=motor, count=t.whole("count", 1, minimum=1), gearing=t.num("gearing", 1.0, positive=True),
        wheel_inertia=wheel_inertia, added_inertia=added, control=control, target_rps=target, voltage=voltage,
        kP=t.num("kP", 0.0, nonneg=True), kS=t.num("kS", 0.0, nonneg=True), kV=t.num("kV", None, positive=True),
        stator_limit=t.num("stator_limit", None, positive=True), supply_limit=t.num("supply_limit", None, positive=True),
        coupled_to=coupled, ratio=ratio)


def _parse_wheel(name, raw) -> Wheel:
    t = _Table("wheel", f"[[wheel]] '{name}'", raw, name)
    tread = t.text("tread", "35A")  # estimate: a common shooter-wheel durometer
    if tread not in PRESETS["wheels"]:
        raise t.err(i18n.t("{table} tread {tread} is not a tread preset; known treads: {known}", table=t.label,
                           tread=repr(tread), known=", ".join(PRESETS["wheels"])), "tread")
    return Wheel(name=name, shaft=t.text("shaft"), center=t.vec("center", length=True),
                 axis=_unit_vec(t, "axis", t.vec("axis", 3, (0.0, 1.0, 0.0))),
                 diameter=t.num("diameter", positive=True, length=True), width=t.num("width", positive=True, length=True),
                 tread=tread)


def _parse_plate(name, raw) -> Plate:
    t = _Table("plate", f"[[plate]] '{name}'", raw, name)
    return Plate(name=name, center=t.vec("center", length=True), size=t.vec("size", length=True, positive=True),
                 rot_deg=t.vec("rot_deg", 3, (0.0, 0.0, 0.0)), material=_material(t),
                 surface_speed=t.num("surface_speed", 0.0))


def _parse_bar(name, raw) -> Bar:
    t = _Table("bar", f"[[bar]] '{name}'", raw, name)
    ends = t.has("a") or t.has("b")
    placed = t.has("center") or t.has("axis") or t.has("length")
    if ends and placed:
        raise t.err(i18n.t("{table} gives both ends ({ends}) and a position ({position}); give one",
                           table=t.label, ends="a, b", position="center, axis, length"))
    if ends:
        a, b = t.vec("a", length=True), t.vec("b", length=True)
    else:
        c = t.vec("center", length=True)
        ax = _unit_vec(t, "axis", t.vec("axis"))
        h = t.num("length", positive=True, length=True) / 2
        a, b = (tuple(ci - h * x for ci, x in zip(c, ax)), tuple(ci + h * x for ci, x in zip(c, ax)))
    if a == b:
        raise t.err(i18n.t("{table} has both ends at the same point", table=t.label), "b")
    spring = t.num("spring", None, positive=True)
    if spring is None and t.has("travel"):
        raise t.err(i18n.t("{table} has travel but no spring", table=t.label), "travel")
    travel = t.num("travel", positive=True, length=True) if spring is not None else 0.0
    return Bar(name=name, a=a, b=b, diameter=t.num("diameter", positive=True, length=True), material=_material(t),
               spring=spring, travel=travel, spring_axis=_unit_vec(t, "spring_axis", t.vec("spring_axis", 3, (0.0, 0.0, 1.0))))


def _parse_curve(name, raw) -> Curve:
    t = _Table("curve", f"[[curve]] '{name}'", raw, name)
    if t.has("radius") and (t.has("radius_start") or t.has("radius_end")):
        raise t.err(i18n.t("{table} has radius with radius_start or radius_end; give radius, or the two ends",
                           table=t.label))
    if t.has("radius"):
        rs = re = t.num("radius", positive=True, length=True)
    else:
        rs = t.num("radius_start", positive=True, length=True)
        re = t.num("radius_end", positive=True, length=True)
    thickness = t.num("thickness", THICKNESS_DEFAULT, positive=True, length=True)
    if min(rs, re) < thickness:
        raise t.err(i18n.t("{table} has a radius ({radius} m) smaller than its thickness ({thickness} m)",
                           table=t.label, radius=f"{min(rs, re):.4g}", thickness=f"{thickness:.4g}"))
    start, end = t.num("start_deg"), t.num("end_deg")
    if end == start:
        raise t.err(i18n.t("{table} has a zero angle span (start_deg equals end_deg)", table=t.label))
    if abs(end - start) > 360.0:
        raise t.err(i18n.t("{table} spans {span} degrees; the angle span must be at most 360", table=t.label,
                           span=f"{abs(end - start):g}"))
    axis = _unit_vec(t, "axis", t.vec("axis", 3, (0.0, 1.0, 0.0)))
    zd = t.vec("zero_dir", 3, None)
    if zd is None:
        zd = (1.0, 0.0, 0.0) if abs(axis[0]) < 0.9 else (0.0, 0.0, 1.0)
    dot = sum(a * z for a, z in zip(axis, zd))
    zd = _unit_vec(t, "zero_dir", tuple(z - dot * a for a, z in zip(axis, zd)))  # drop the part along the axis
    return Curve(name=name, center=t.vec("center", length=True), axis=axis, zero_dir=zd, radius_start=rs, radius_end=re,
                 start_deg=start, end_deg=end, width=t.num("width", positive=True, length=True), thickness=thickness,
                 material=_material(t))


def _parse_cad(name, raw, base_dir: Path) -> Cad:
    t = _Table("cad", f"[[cad]] '{name}'", raw, name)
    part, mesh = _resolve_path(t, "part", base_dir), _resolve_path(t, "mesh", base_dir)
    if (part is None) == (mesh is None):
        raise t.err(i18n.t("{table} needs exactly one of {part_setting} or {mesh_setting}", table=t.label,
                           part_setting="part = \"<part folder>\"", mesh_setting="mesh = \"<file.stl>\""))
    slots = t.t.get("slots", {})
    if not isinstance(slots, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in slots.items()):
        raise t.err(i18n.t("{table} slots must be a table of slot name = shaft name, not {value}", table=t.label,
                           value=repr(slots)))
    tread = None
    if mesh is not None:
        for key in ("slots", "tread"):
            if t.has(key):
                raise t.err(i18n.t("{table} has {key}, but a mesh doesn't move: {key} is for a part folder's turning "
                                   "wheels", table=t.label, key=key))
    else:
        tread = t.text("tread", "35A")  # estimate: a common shooter-wheel durometer, as for [[wheel]]
        if tread not in PRESETS["wheels"]:
            raise t.err(i18n.t("{table} tread {tread} is not a tread preset; known treads: {known}", table=t.label,
                               tread=repr(tread), known=", ".join(PRESETS["wheels"])), "tread")
    return Cad(name=name, part=part, mesh=mesh, units=_units(t, mesh is not None),
               center=t.vec("center", 3, (0.0, 0.0, 0.0), length=True), rot_deg=t.vec("rot_deg", 3, (0.0, 0.0, 0.0)),
               material=_material(t), slots=dict(slots), tread=tread)


def _parse_inlet(name, raw) -> Inlet:
    t = _Table("inlet", f"[[inlet]] '{name}'", raw, name)
    return Inlet(name=name, center=t.vec("center", length=True), size=t.vec("size", length=True, positive=True),
                 count=t.whole("count", 1, minimum=1), rate=t.num("rate", 5.0, positive=True),
                 velocity=t.vec("velocity", 3, (0.0, 0.0, 0.0)), spin=t.vec("spin", 3, (0.0, 0.0, 0.0)),
                 spread_speed=t.num("spread_speed", 0.0, nonneg=True),
                 spread_position=t.num("spread_position", 0.0, nonneg=True, length=True),
                 spread_time=t.num("spread_time", 0.0, nonneg=True), seed=t.whole("seed", 1))


def _parse_outlet(name, raw) -> Outlet:
    t = _Table("outlet", f"[[outlet]] '{name}'", raw, name)
    return Outlet(name=name, center=t.vec("center", length=True), size=t.vec("size", length=True, positive=True))


def _parse_flight(raw, known: list[str], base_dir: Path) -> Flight:
    """The [flight]; `known` is the names of the file's outlets, `base_dir` the folder a goal's range is read from."""
    t = _Table("flight", "[flight]", raw)
    outlet = t.text("outlet")
    if outlet not in known:
        raise t.err(i18n.t("{table} outlet {outlet} is not defined; outlets: {known}", table=t.label,
                           outlet=repr(outlet), known=", ".join(known) or i18n.t("(none)")), "outlet")
    air = t.text("air", "default")
    if air not in PRESETS["air"]:
        raise t.err(i18n.t("{table} air {air} is not an air preset; known: {known}", table=t.label, air=repr(air),
                           known=", ".join(PRESETS["air"])), "air")
    goal = _flight_goal(t, base_dir) if "goal" in t.t else None
    if goal is None:
        for key in ("at", "heading_deg"):
            if t.has(key):
                raise t.err(i18n.t("{table} {key} places the bench on a range's field for a goal; give {goal_setting} "
                                   "too, or leave {key} out", table=t.label, key=key,
                                   goal_setting="goal = { range = \"<range file>\", name = \"<goal>\" }"), key)
    if goal is not None:  # the target is optional with a goal, but whole: both of its keys, and a ring only with them
        given = [k for k in ("target_distance", "target_height") if t.has(k)]
        if len(given) == 1:
            other = "target_height" if given[0] == "target_distance" else "target_distance"
            raise t.err(i18n.t("{table} has {key} but not {other}; a target needs both (or, with a goal, neither)",
                               table=t.label, key=given[0], other=other), other)
        if not given and t.has("ring_diameter"):
            raise t.err(i18n.t("{table} has {key} but no target; a ring sits at the target, so give "
                               "{distance_key} and {height_key} too", table=t.label, key="ring_diameter",
                               distance_key="target_distance", height_key="target_height"), "ring_diameter")
    target = _REQ if goal is None else None
    return Flight(outlet=outlet, height=t.num("height", 0.0, nonneg=True, length=True) if goal is None else goal.at[2],
                  target_distance=t.num("target_distance", target, positive=True, length=True),
                  target_height=t.num("target_height", target, nonneg=True, length=True),
                  ring_diameter=t.num("ring_diameter", None, positive=True, length=True), air=air, goal=goal)


_GOAL_KEYS = ("range", "name")


def _flight_goal(t: _Table, base_dir: Path) -> FlightGoal:
    """`goal = { range, name }` with `at` and `heading_deg`: the goal, read from its range file (relative to the bench
    file, or a built-in range's name), and the solid things on that field."""
    g = t.t["goal"]
    if not isinstance(g, dict) or set(g) != set(_GOAL_KEYS) or not all(isinstance(g[k], str) for k in _GOAL_KEYS):
        raise t.err(i18n.t("{table} goal must be {goal_setting}, not {value}", table=t.label, value=repr(g),
                           goal_setting="{ range = \"<range file>\", name = \"<goal>\" }"), "goal")
    for key in ("at", "heading_deg"):
        if not t.has(key):
            raise t.err(i18n.t("{table} has a goal, so it needs {at_setting} (the bench origin's place on the field "
                               "and its height, m) and {heading_setting} (which way the bench's +x faces on the "
                               "field); {key} is missing", table=t.label, key=key, at_setting="at = [x, y, z]",
                               heading_setting="heading_deg"), key)
    if t.has("height"):
        raise t.err(i18n.t("{table} has a goal, so the bench origin's height is {at_setting}'s z; leave {key} out",
                           table=t.label, key="height", at_setting="at"), "height")
    at = t.vec("at", 3, length=True)
    if at[2] < 0:
        raise t.err(i18n.t("{table} at's height must be zero or more, not {value}", table=t.label,
                           value=repr(t.t.get("at", t.t.get("at_in")))), "at")
    heading = t.num("heading_deg")
    ref, name = g["range"], g["name"]
    path = BUILTIN_RANGES.get(ref) or base_dir / ref
    if not path.is_file():
        raise t.err(i18n.t("{table} goal range {range} is not a file (it is read from the bench file's folder: "
                           "{folder})", table=t.label, range=repr(ref), folder=base_dir), "goal")
    try:
        rng = load_range(ref, base_dir)
    except RangeError as e:
        raise t.err(i18n.t("{table} goal range {range}: {error}", table=t.label, range=repr(ref), error=e),
                    "goal") from None
    goals = {x.name: x for x in rng.goals}
    if name not in goals:
        raise t.err(i18n.t("{table} goal {name} is not a goal in {range}; its goals: {goals}", table=t.label,
                           name=repr(name), range=repr(ref), goals=", ".join(goals) or i18n.t("(none)")), "goal")
    from ..testrange.goal_fit import field_boxes    # pulls in mujoco
    boxes = field_boxes(rng)
    return FlightGoal(range=path, name=name, goal=goals[name], at=at, heading_deg=heading, boxes=boxes)


def _parse_run(raw) -> RunSettings:
    t = _Table("run", "[run]", raw)
    return RunSettings(duration=t.num("duration", 5.0, positive=True), timestep=t.num("timestep", None, positive=True),
                       jam_time=t.num("jam_time", 1.0, positive=True),
                       jam_distance=t.num("jam_distance", 0.01, positive=True, length=True))
