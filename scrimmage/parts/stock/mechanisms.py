"""Stock mechanisms: elevator, arm, flywheel, roller, turret, cylinder, launcher and disc shooter. They carry mass,
inertia, joints and motor/encoder slots. Their geoms touch nothing unless the part sets collide = true; then all but
the drawing-only ones (the launcher's hood arc, the disc shooter's guide plates, a push cylinder's barrel) touch the
field, game pieces and a partner robot, never the robot's own parts."""

from __future__ import annotations

import math

from ...motors import MOTORS
from ...pneumatics.model import DEFAULT_CONDUCTANCE
from . import Param, Stock, register
from ...i18n import N_

STEEL = [0.55, 0.55, 0.6, 1.0]
ORANGE = [0.95, 0.5, 0.1, 1.0]

_MOTOR_NAMES = tuple(sorted(MOTORS))


def _motor_param(doc: str) -> Param:
    return Param("motor", "", "KrakenX60", doc, kind="str", choices=_MOTOR_NAMES)


def _count_param(doc: str, default: int) -> Param:
    return Param("count", "", default, doc, kind="int", min=1)


COLLIDE = Param("collide", "", False, N_("its geoms touch the field, game pieces and a partner robot (never the "
                "robot's own parts). Drawing-only geoms never collide: the launcher's hood arc, a disc shooter's "
                "guide plates, and a push cylinder's barrel (a push cylinder touches nothing even with collide on)"),
                kind="bool")


# ---------------------------------------------------------------- elevator

def slide_chain(p: dict, stage_geoms, last: str, last_mass: float, slot: str, encoder_slot: str,
                limit_solref: list[float] | None = None) -> dict:
    """A telescope of p["stages"] moving bodies on z slides, each stroke = travel / stages above the one below:
    "base", then stage1 .. stage<n-1> and `last` (the carriage or the climber's tip), all coupled to stage1 and
    driven by a spool (the elevator's rigging, gearing, spool_radius, friction, motor and count). stage_geoms(i, h)
    gives body i's geoms (0 the base, n the last), h the stroke + 0.15 m an elevator's stages are drawn tall.
    limit_solref, if given, stiffens every stage's travel stops (the climber's: a robot hangs on them).
    Returns the part's body, coupling, motor and encoder entries."""
    n, travel = p["stages"], p["travel"]
    stroke = round(travel / n, 9)                      # each moving stage travels this far relative to the one below
    h = stroke + 0.15
    bodies = [{"name": "base", "mass": p["stage_mass"], "geom": stage_geoms(0, h)}]
    parent = "base"
    names = [f"stage{i}" for i in range(1, n)] + [last]
    for i, name in enumerate(names, start=1):
        joint = {"name": name, "slide": "z", "range": [0.0, stroke]}
        if limit_solref is not None:
            joint["limit_solref"] = list(limit_solref)
        if i == 1:
            joint["frictionloss"] = p["friction"] * n   # n x on the first stage = `friction` newtons at the last
        bodies.append({"name": name, "parent": parent, "mass": last_mass if name == last else p["stage_mass"],
                       "joint": joint, "geom": stage_geoms(i, h)})
        parent = name
    r = p["spool_radius"]
    cascade = p["rigging"] == "cascade"
    first = names[0]
    return {
        "body": bodies,
        "coupling": [{"joints": [first, other], "ratio": 1.0} for other in names[1:]],
        "motor": [{"slot": slot, "joint": first, "motor": p["motor"], "count": p["count"],
                   "gearing": p["gearing"] / r if cascade else p["gearing"] * n / r}],
        "encoder": [{"slot": encoder_slot, "joint": first,
                     "ratio": 1 / (2 * math.pi * r) if cascade else n / (2 * math.pi * r)}],
    }


def _elevator(p: dict) -> dict:
    n, w, c = p["stages"], p["width"], p["collide"]

    def geoms(i: int, h: float) -> list[dict]:
        if i == 0:
            return [{"box": [0.05, w, h], "pos": [0, 0, h / 2], "color": STEEL, "collide": c}]
        if i == n:
            return [{"box": [0.08, w, 0.1], "pos": [0, 0, 0.05], "color": ORANGE, "collide": c}]
        return [{"box": [0.05, w - 0.02 * i, h], "pos": [0, 0, h / 2], "color": STEEL, "collide": c}]

    return {
        "part": {"name": "elevator", "format": 1},
        **slide_chain(p, geoms, "carriage", p["carriage_mass"], "lift", "encoder"),
        "mount": [{"name": "base", "body": "base"},
                  {"name": "carriage", "body": "carriage", "pos": [0.04, 0, 0]}],
    }


register(Stock(
    "elevator", N_("linear lift of 1-4 stages driven by a spool; the carriage is the last stage"),
    (Param("travel", "m", 1.0, N_("total carriage travel from fully lowered to fully raised"), positive=True),
     Param("stages", "", 1, N_("number of moving stages including the carriage (the carriage is stage 'carriage', "
           "the others are stage1, stage2, ...)"), kind="int", min=1, max=4),
     Param("rigging", "", "continuous", N_("continuous: the carriage moves at the rope speed, full force at the carriage, "
           "and the first stage moves at 1/stages of it; cascade: the first stage moves at the rope speed and the "
           "carriage at stages x rope speed, with 1/stages of the force (carriage height = spool turns x 2 pi r x stages)"),
           kind="str", choices=("continuous", "cascade")),
     Param("carriage_mass", "kg", 3.0, N_("mass of the carriage and what it carries"), positive=True),
     Param("stage_mass", "kg", 1.5, N_("mass of the base and of each intermediate stage"), positive=True),
     _motor_param("motor model"),
     _count_param("motors driving the lift", 2),
     Param("gearing", "", 9.0, N_("motor turns per spool turn"), positive=True),
     Param("spool_radius", "m", 0.022, N_("radius the rope winds on"), positive=True),
     Param("friction", "N", 5.0, N_("constant sliding friction, in newtons felt at the carriage"), min=0.0),
     Param("width", "m", 0.3, N_("side-to-side width of the stages"), min=0.1),
     COLLIDE),
    _elevator, slots=("lift", "encoder"), mounts=("base", "carriage"),
    note=N_("Slots: lift (motor), encoder (spool turns). The elevator stands up from its base mount; the carriage "
         "starts fully lowered.")))


# ---------------------------------------------------------------- arm

def _arm(p: dict) -> dict:
    L, m = p["length"], p["mass"]
    com = p["com"] or L / 2
    lo, hi = p["range"]
    joint = {"name": "pivot", "hinge": [0, -1, 0], "range": [lo, hi]}
    if p["spring_stiffness"] > 0 or p["spring_rest"] != 0:
        joint["spring"] = {"stiffness": p["spring_stiffness"], "rest": p["spring_rest"]}
    return {
        "part": {"name": "arm", "format": 1},
        "body": [
            {"name": "base", "mass": 0.5,
             "geom": [{"box": [0.06, 0.06, 0.06], "color": STEEL, "collide": p["collide"]}]},
            {"name": "arm", "parent": "base", "mass": m, "joint": joint,
             "com": [com, 0, 0], "inertia": [m * 0.04 ** 2 / 6, m * L ** 2 / 12, m * L ** 2 / 12],
             "geom": [{"box": [L, 0.04, 0.04], "pos": [L / 2, 0, 0], "color": ORANGE, "collide": p["collide"]}]},
        ],
        "motor": [{"slot": "pivot", "joint": "pivot", "motor": p["motor"], "count": p["count"],
                   "gearing": p["gearing"]}],
        "encoder": [{"slot": "encoder", "joint": "pivot", "ratio": 1.0}],
        "mount": [{"name": "base", "body": "base"},
                  {"name": "tip", "body": "arm", "pos": [L, 0, 0]}],
    }


register(Stock(
    "arm", N_("single-jointed arm on a horizontal pivot; 0 degrees points forward, positive raises the tip"),
    (Param("length", "m", 0.5, N_("pivot to tip"), positive=True),
     Param("mass", "kg", 2.0, N_("arm mass"), positive=True),
     Param("com", "m", 0.0, N_("center of mass distance from the pivot; 0 means half the length. Only the center-of-mass point moves: "
           "the inertia stays that of a uniform bar of this mass and length"),
           min=0.0, max=lambda q: q["length"]),
     Param("range", "deg", [-90.0, 90.0], N_("lowest and highest allowed angle"), kind="pair"),
     _motor_param("motor model"),
     _count_param("motors driving the pivot", 1),
     Param("gearing", "", 50.0, N_("motor turns per arm turn"), positive=True),
     Param("spring_stiffness", "N*m/rad", 0.0, N_("torsion spring (gas spring or counterbalance) stiffness"), min=0.0),
     Param("spring_rest", "deg", 0.0, N_("angle at which the spring is relaxed (a spring is added whenever this or the stiffness is non-zero)")),
     COLLIDE),
    _arm, slots=("pivot", "encoder"), mounts=("base", "tip"),
    note=N_("Slots: pivot (motor), encoder (arm turns).")))


# ---------------------------------------------------------------- turret

def _turret(p: dict) -> dict:
    from ..model import PartError
    lo, hi = p["range"]
    if not (-360 <= lo and hi <= 360):
        raise PartError(f"turret: range {[lo, hi]} has an end beyond 360 degrees: keep both ends within +-360")
    r, m = p["diameter"] / 2, p["mass"]
    return {
        "part": {"name": "turret", "format": 1},
        "body": [
            {"name": "base", "mass": 0.5,
             "geom": [{"box": [0.06, 0.06, 0.06], "color": STEEL, "collide": p["collide"]}]},
            {"name": "platform", "parent": "base", "mass": m,
             "joint": {"name": "turn", "hinge": [0, 0, 1], "range": [lo, hi], "frictionloss": p["friction"]},
             "com": [0, 0, 0], "inertia": [m * r ** 2 / 4, m * r ** 2 / 4, m * r ** 2 / 2],
             "geom": [{"cylinder": [r, 0.02], "color": ORANGE, "collide": p["collide"]}]},
        ],
        "motor": [{"slot": "turn", "joint": "turn", "motor": p["motor"], "count": p["count"],
                   "gearing": p["gearing"]}],
        "encoder": [{"slot": "encoder", "joint": "turn", "ratio": 1.0}],
        "mount": [{"name": "base", "body": "base"},
                  {"name": "top", "body": "platform", "pos": [0, 0, p["height"]]}],
    }


register(Stock(
    "turret", N_("a flat platform on a vertical axis; carry a launcher on it and turn it to aim"),
    (Param("diameter", "m", 0.3, N_("the platform's diameter"), positive=True),
     Param("mass", "kg", 2.0, N_("platform mass"), positive=True),
     Param("height", "m", 0.05, N_("the top mount's height above the base"), min=0.0),
     Param("range", "deg", [-180.0, 180.0], N_("lowest and highest allowed angle (each end within +-360)"), kind="pair"),
     _motor_param("motor model"),
     _count_param("motors driving the turret", 1),
     Param("gearing", "", 60.0, N_("motor turns per turret turn"), positive=True),
     Param("friction", "N*m", 0.2, N_("constant friction torque on the turret's joint"), min=0.0),
     COLLIDE),
    _turret, slots=("turn", "encoder"), mounts=("base", "top"),
    note=N_("Slots: turn (motor), encoder (turret turns). 0 faces the part's +x; positive turns left (counter-clockwise "
         "from above). Attach the launcher to turret.top.")))


# ---------------------------------------------------------------- pneumatic cylinder

BRASS = [0.8, 0.7, 0.35, 1.0]
CYLINDER_WALL = 0.004    # m the barrel is drawn wider than its bore


def _cylinder(p: dict) -> dict:
    """A double-acting cylinder along the part's +x, the rod extending toward +x. Free form (push empty): a rod
    body on the slide "extend". Push form: the barrel alone, its rod's end pinned to another part's body. Either way
    the assembler adds the tendon between the pins that carries the air's force (parts/assemble.py)."""
    from ..assemble import CYLINDER_STOP_SOLREF
    from ..model import PartError
    push, stroke = p["push"], p["stroke"]
    length = p["length"] or 0.3 * stroke + 0.05     # drawn length; the push form's real one may come from the pose
    # a push cylinder's barrel is drawn only: it is fixed to its mount and doesn't turn to follow its rod's end
    barrel = {"cylinder": [p["bore"] / 2 + CYLINDER_WALL, length], "axis": "x", "pos": [length / 2, 0, 0],
              "color": STEEL, "collide": p["collide"] and not push}
    cylinder = {"bore": p["bore"], "rod": p["rod"], "stroke": stroke, "friction": p["friction"],
                "conductance": p["conductance"], "pin_a": {"body": "barrel"}}
    if p["length"]:
        cylinder["length"] = p["length"]
    if push:
        if push.count(".") != 1 or not all(push.split(".")):
            raise PartError(f"cylinder: push must be \"<part>.<body>\" (the body the rod's end is pinned to), "
                            f"not {push!r}")
        cylinder["pin_b"] = {"body": push, "pos": p["push_pos"]}
        return {"part": {"name": "cylinder", "format": 1},
                "body": [{"name": "barrel", "mass": p["mass"] + p["rod_mass"], "geom": [barrel]}],
                "cylinder": cylinder, "mount": [{"name": "base", "body": "barrel"}]}
    if any(p["push_pos"]):
        raise PartError("cylinder: push_pos places the rod's end on the part named by push; set push too, "
                        "or remove push_pos")
    rod_len = 0.8 * length
    cylinder["pin_b"] = {"body": "rod"}
    cylinder["length"] = length
    return {"part": {"name": "cylinder", "format": 1},
            "body": [{"name": "barrel", "mass": p["mass"], "geom": [barrel]},
                     {"name": "rod", "parent": "barrel", "mass": p["rod_mass"], "pos": [length, 0, 0],
                      "joint": {"name": "extend", "slide": "x", "range": [0.0, stroke],
                                "limit_solref": list(CYLINDER_STOP_SOLREF)},
                      "geom": [{"cylinder": [p["rod"] / 2, rod_len], "axis": "x", "pos": [-rod_len / 2, 0, 0],
                                "color": BRASS, "collide": p["collide"]}]}],
            "cylinder": cylinder,
            "mount": [{"name": "base", "body": "barrel"}, {"name": "rod", "body": "rod"}]}


register(Stock(
    "cylinder", N_("a double-acting pneumatic cylinder, driven by air through its valve"),
    (Param("bore", "m", 0.0269875, N_("the piston's diameter (1-1/16 in; estimate, UNCALIBRATED)"), positive=True),
     Param("rod", "m", 0.0079375, N_("the rod's diameter, less than the bore (5/16 in; UNCALIBRATED)"), positive=True),
     Param("stroke", "m", 0.1524, N_("how far the rod travels (6 in)"), positive=True),
     Param("friction", "N", 8.0, N_("seal friction (estimate, UNCALIBRATED)"), min=0.0),
     Param("conductance", "m^3/(s*Pa)", DEFAULT_CONDUCTANCE,
           N_("the valve's sonic conductance, how freely air flows (estimate, UNCALIBRATED)"), positive=True),
     Param("measured", "", "", N_("a cylinder measurement file (\"deploy.measure.toml\", relative to the robot config) "
           "whose timed strokes fit the conductance; empty for the conductance above"), kind="text"),
     Param("mass", "kg", 0.25, N_("the barrel's mass (UNCALIBRATED)"), positive=True),
     Param("rod_mass", "kg", 0.1, N_("the rod's mass (UNCALIBRATED)"), positive=True),
     Param("push", "", "", N_("\"<part>.<body>\" the rod's end is pinned to (a stock arm's \"arm.arm\"); empty for a "
           "free rod with its own mount"), kind="text"),
     Param("push_pos", "m", [0.0, 0.0, 0.0], N_("where on the push body the rod's end is pinned, in that body's frame"),
           kind="vec3"),
     Param("length", "m", 0.0, N_("pin to pin when retracted; 0 takes it from the assembled pose (push) or "
           "0.3 x stroke + 0.05 m (free)"), min=0.0),
     COLLIDE),
    _cylinder, slots=("valve",), mounts=("base", "rod"),
    note=N_("Slots: valve (a PCM or PH solenoid). The barrel lies along the part's +x and the rod extends toward +x. "
         "Free (push empty): mount things on cylinder.rod. Push: the rod's end is pinned to push at push_pos, and "
         "the cylinder drives the joints between its two ends; it has only the base mount.")))


# ---------------------------------------------------------------- flywheel and roller

def _spinner(p: dict, name: str, length: float) -> dict:
    r, m = p["diameter"] / 2, p["mass"]
    moi = p["moi"] or m * r * r / 2
    return {
        "part": {"name": name, "format": 1},
        "body": [
            {"name": "base", "mass": 0.2,
             "geom": [{"box": [0.05, length, 0.05], "color": STEEL, "collide": p["collide"]}]},
            {"name": "wheel", "parent": "base", "mass": m, "joint": {"name": "spin", "hinge": "y"},
             "com": [0, 0, 0], "inertia": [moi / 2, moi, moi / 2],
             "geom": [{"cylinder": [r, length], "axis": "y", "color": ORANGE, "collide": p["collide"]}]},
        ],
        "motor": [{"slot": "spin", "joint": "spin", "motor": p["motor"], "count": p["count"],
                   "gearing": p["gearing"]}],
        "mount": [{"name": "base", "body": "base"}],
    }


def _flywheel(p: dict) -> dict:
    return _spinner(p, "flywheel", p["width"])


def _roller(p: dict) -> dict:
    return _spinner(p, "roller", p["length"])


def _spinner_params(diameter: float, mass: float, extent: Param) -> tuple[Param, ...]:
    return (Param("diameter", "m", diameter, N_("wheel diameter"), positive=True),
            Param("mass", "kg", mass, N_("wheel mass"), positive=True),
            Param("moi", "kg*m^2", 0.0, N_("moment of inertia about the axle; 0 means a solid disc, 1/2 m r^2"),
                  min=0.0),
            extent,
            _motor_param("motor model"),
            _count_param("motors driving the wheel", 1),
            Param("gearing", "", 1.0, N_("motor turns per wheel turn"), positive=True),
            COLLIDE)


register(Stock(
    "flywheel", N_("spinning wheel on a horizontal axle (shooter wheel)"),
    _spinner_params(0.1016, 0.5, Param("width", "m", 0.05, N_("wheel thickness along the axle"), positive=True)),
    _flywheel, slots=("spin",), mounts=("base",), note=N_("Slot: spin (motor). The axle runs along the y axis.")))

register(Stock(
    "roller", N_("spinning roller on a horizontal axle (intake or indexer)"),
    _spinner_params(0.05, 0.3, Param("length", "m", 0.4, N_("roller length along the axle"), positive=True)),
    _roller, slots=("spin",), mounts=("base",), note=N_("Slot: spin (motor). The axle runs along the y axis.")))


# ---------------------------------------------------------------- launcher

HOOD_PLATE = 0.006                  # m, the hood's drawn thickness
SIDE_PLATE = 0.006                  # m, each side plate's drawn thickness
LAUNCHER_FRAME_MASS = 1.0           # kg, the side plates and the frame (the base body)
HOOD_SEGMENT_DEG = 15.0             # the drawn hood's arc is boxes of at most this many degrees


def _hood_arc(r_inner: float, width: float, start_deg: float, span_deg: float) -> list[dict]:
    """Boxes along the hood's arc, from start_deg (the path angle phi, from +x toward +z) round span_deg further.
    Drawn only, even with collide = true: the launcher model, not contact, carries the ball round the hood."""
    n = max(1, math.ceil(span_deg / HOOD_SEGMENT_DEG))
    step = span_deg / n
    rc = r_inner + HOOD_PLATE / 2
    chord = 2 * rc * math.sin(math.radians(step) / 2)
    out = []
    for k in range(n):
        phi = start_deg + (k + 0.5) * step
        a = math.radians(phi)
        # pitch -phi about y turns the box's x axis to (cos phi, 0, sin phi): radial
        out.append({"box": [HOOD_PLATE, width, chord], "pos": [rc * math.cos(a), 0, rc * math.sin(a)],
                    "rot": [0, -phi, 0], "color": STEEL, "collide": False})
    return out


def _launcher(p: dict) -> dict:
    r, w = p["wheel_diameter"] / 2, p["width"]
    r_hood = r + p["hood_gap"]                      # the hood's inner face
    side = 2 * (r_hood + 0.03)
    moi = p["wheel_moi"]
    lo, hi = p["hood_range"]
    articulated = p["hood"] == "articulated"
    entry = p["hood_entry_deg"]
    plates = [{"box": [side, SIDE_PLATE, side], "pos": [0, s * (w + SIDE_PLATE) / 2, 0], "color": STEEL,
               "collide": p["collide"]} for s in (1, -1)]
    if articulated:   # drawn over the wrap the hood keeps at its high stop, so it never reaches past the entry
        hood_geoms = _hood_arc(r_hood, w, lo + 90.0, max(entry - (hi + 90.0), 1.0))
    else:
        hood_geoms = _hood_arc(r_hood, w, p["hood_angle"] + 90.0, max(entry - (p["hood_angle"] + 90.0), 1.0))
    bodies = [
        {"name": "base", "mass": LAUNCHER_FRAME_MASS, "geom": plates + ([] if articulated else hood_geoms)},
        # the wheel's mass is a solid disc's of this inertia; added_moi turns with it in the launcher model only
        {"name": "wheel", "parent": "base", "mass": 2 * moi / (r * r), "joint": {"name": "spin", "hinge": "y"},
         "com": [0, 0, 0], "inertia": [moi / 2, moi, moi / 2],
         "geom": [{"cylinder": [r, w], "axis": "y", "color": ORANGE, "collide": p["collide"]}]},
    ]
    out = {
        "part": {"name": "launcher", "format": 1},
        "body": bodies,
        "motor": [{"slot": "shooter", "joint": "spin", "motor": p["motor"], "count": p["count"],
                   "gearing": p["gearing"]}],
        "mount": [{"name": "base", "body": "base"}],
    }
    if articulated:
        # hinge -y, as the arm's pivot: positive raises the exit end. The joint reads 0 at the low stop.
        bodies.append({"name": "hood", "parent": "base", "mass": p["hood_mass"],
                       "joint": {"name": "hood", "hinge": [0, -1, 0], "range": [0.0, hi - lo]}, "geom": hood_geoms})
        out["motor"].append({"slot": "hood", "joint": "hood", "motor": p["hood_motor"], "count": p["hood_count"],
                             "gearing": p["hood_gearing"]})
        out["encoder"] = [{"slot": "hood_encoder", "joint": "hood", "ratio": 1.0}]
    return out


register(Stock(
    "launcher", N_("hooded shooter: a wheel on a horizontal shaft and a hood around it, articulated or fixed; "
    "the test range's [shooter] runs it with sub-project 8's launcher model"),
    (Param("wheel_diameter", "m", 0.1016, N_("shooter wheel diameter"), positive=True),
     Param("width", "m", 0.3, N_("inside width between the side plates; every lane's ball must fit"), positive=True),
     Param("wheel_moi", "kg*m^2", 0.002, N_("the wheels, hubs and shaft's moment of inertia about the shaft"),
           positive=True),
     Param("added_moi", "kg*m^2", 0.0, N_("a flywheel on the same shaft"), min=0.0),
     _motor_param("shooter motor model"),
     _count_param("motors driving the shooter shaft", 1),
     Param("gearing", "", 1.0, N_("shooter motor turns per wheel turn"), positive=True),
     Param("hood_gap", "m", 0.12, N_("gap between the wheel's surface and the hood; the ball is squeezed by its "
           "diameter less this"), positive=True),
     Param("hood_entry_deg", "deg", 240.0, N_("where the ball enters, round the shaft from forward (+x) toward up"),
           min=0.0, max=360.0),
     Param("hood", "", "articulated", N_("articulated: the hood turns about the shaft on a motor (joint hood); "
           "fixed: the hood stays at hood_angle"), kind="str", choices=("articulated", "fixed")),
     Param("hood_angle", "deg", 50.0, N_("the fixed hood's exit angle, above horizontal"), min=-90.0, max=90.0),
     Param("hood_range", "deg", [20.0, 70.0], N_("the articulated hood's exit angles at its low and high hard stops"),
           kind="pair"),
     Param("hood_motor", "", "KrakenX60", N_("hood motor model"), kind="str", choices=_MOTOR_NAMES),
     Param("hood_count", "", 1, N_("motors driving the hood"), kind="int", min=1),
     Param("hood_gearing", "", 50.0, N_("hood motor turns per hood turn"), positive=True),
     Param("hood_mass", "kg", 1.0, N_("the articulated hood's mass"), positive=True),
     COLLIDE),
    _launcher, slots=("shooter", "hood", "hood_encoder"), mounts=("base",),
    note=N_("Slots: shooter (motor on joint spin), hood (motor) and hood_encoder (hood turns), the last two only with "
         "an articulated hood. The base's origin is on the shaft (x forward, z up, the shaft along y). The hood "
         "joint reads 0 at the low stop: exit angle = hood_range[0] + the joint's angle. In the world the "
         "[shooter] section's launcher model runs the shaft and the spin joint mirrors it.")))


# ---------------------------------------------------------------- disc shooter

DISC_FRAME_MASS = 1.5               # kg, the base: the frame the channel hangs from
DISC_PLATE = 0.006                  # m, the guide plates' and the fence's drawn thickness
DISC_GAP = 0.04                     # m, the gap between the guide plates (the disc is 0.036 m thick)
GROUPS = ("single", "per_side", "per_wheel")


def disc_wheel_spots(p: dict) -> list[dict]:
    """The wheels of a disc_shooter's parameters, as the disc launcher's [layout] wheels: {x, side, group}, x from
    the channel's entry downrange. wheel_count wheels per side from wheel_start every wheel_pitch: on wheel_side for
    a linear channel or an arc (which has one), on both sides for a pinch. groups: one shaft for all ("single"), one
    per side (a pinch's "per_side"; a one-sided layout has one side), or one per wheel ("per_wheel", in order along
    the channel, the right side's first)."""
    from ..model import PartError
    n = p["wheel_count"]
    if p["layout"] == "arc" and n != 1:
        raise PartError(f"disc_shooter: an arc has one wheel (the fence curves round it); wheel_count is {n}, make "
                        "it 1")
    sides = ("right", "left") if p["layout"] == "pinch" else (p["wheel_side"],)
    spots = []
    for s, side in enumerate(sides):
        for k in range(n):
            x = p["wheel_start"] + k * p["wheel_pitch"]
            group = {"single": 0, "per_side": s, "per_wheel": len(spots)}[p["groups"]]
            spots.append({"x": round(x, 12), "side": side, "group": group})
    return spots


def disc_wheel_y(p: dict, side: str) -> float:
    """A wheel center's distance across the channel (y, left positive): R_w + r - c from the center line, the squeeze
    c shared between the two sides in a pinch (disc/launcher.py)."""
    c = p["compression"] / 2 if p["layout"] == "pinch" else p["compression"]
    offset = p["wheel_diameter"] / 2 + p["channel_width"] / 2 - c
    return offset if side == "left" else -offset


def _disc_channel_geoms(p: dict) -> list[dict]:
    """The guide plates above and below the disc, and the fence opposite the wheels (an arc's curves round its
    wheel). The guide plates are drawn only, even with collide = true: the disc launcher model holds the disc
    between them."""
    L, r, w = p["channel_length"], p["channel_width"] / 2, p["channel_width"]
    length = L + 2 * r                          # from behind the entry to the exit guide's end
    geoms = [{"box": [length, w, DISC_PLATE], "pos": [length / 2 - r, 0, s * (DISC_GAP + DISC_PLATE) / 2],
              "color": STEEL, "collide": False} for s in (1, -1)]
    fence_side = -1 if p["wheel_side"] == "right" else 1
    if p["layout"] == "linear":
        geoms.append({"box": [L, DISC_PLATE, DISC_GAP], "pos": [L / 2, fence_side * (r + DISC_PLATE / 2), 0],
                      "color": ORANGE, "collide": p["collide"]})
    elif p["layout"] == "arc":
        # the fence round the wheel, from its entry end round arc_deg to beside the wheel (disc/launcher.py)
        xw, yw = p["wheel_start"], disc_wheel_y(p, p["wheel_side"])
        radius = p["wheel_diameter"] / 2 + p["channel_width"] - p["compression"] + DISC_PLATE / 2
        sigma = 1 if p["wheel_side"] == "left" else -1
        end = -sigma * 90.0                     # the fence's exit end, beside the wheel, seen from its center
        n = max(1, math.ceil(p["arc_deg"] / HOOD_SEGMENT_DEG))
        step = p["arc_deg"] / n
        chord = 2 * radius * math.sin(math.radians(step) / 2)
        for k in range(n):
            phi = end - sigma * (p["arc_deg"] - (k + 0.5) * step)
            a = math.radians(phi)
            geoms.append({"box": [DISC_PLATE, chord, DISC_GAP],
                          "pos": [xw + radius * math.cos(a), yw + radius * math.sin(a), 0], "rot": [0, 0, phi],
                          "color": ORANGE, "collide": p["collide"]})
    return geoms


def _disc_shooter(p: dict) -> dict:
    spots = disc_wheel_spots(p)
    r_w, width = p["wheel_diameter"] / 2, p["wheel_width"]
    lo, hi = p["tilt_range"]
    articulated = p["tilt"] == "articulated"
    tilt0 = lo if articulated else p["tilt_angle"]
    channel = {"name": "channel", "parent": "base", "mass": p["channel_mass"], "rot": [0, -tilt0, 0],
               "geom": _disc_channel_geoms(p)}
    if articulated:
        # hinge -y at the channel's entry, as the launcher's hood: positive raises the exit end. 0 at the low stop.
        channel["joint"] = {"name": "tilt", "hinge": [0, -1, 0], "range": [0.0, hi - lo]}
    bodies = [{"name": "base", "mass": DISC_FRAME_MASS,
               "geom": [{"box": [0.05, 0.05, 0.05], "color": STEEL, "collide": p["collide"]}]}, channel]
    motors = []
    groups = max(s["group"] for s in spots) + 1
    for g in range(groups):
        mine = [s for s in spots if s["group"] == g]
        first = mine[0]
        moi = p["wheel_moi"] * len(mine)
        # the group's first wheel turns on its spin joint; its other wheels are drawn on the channel, still. A
        # right-hand wheel turns clockwise from above (disc/launcher.py), so its joint's axis is -z.
        axis = [0, 0, 1] if first["side"] == "left" else [0, 0, -1]
        bodies.append({"name": f"wheel{g}", "parent": "channel", "mass": 2 * moi / (r_w * r_w),
                       "pos": [first["x"], disc_wheel_y(p, first["side"]), 0],
                       "joint": {"name": f"spin{g}", "hinge": axis}, "com": [0, 0, 0],
                       "inertia": [moi / 2, moi / 2, moi],
                       "geom": [{"cylinder": [r_w, width], "axis": "z", "color": ORANGE,
                                 "collide": p["collide"]}]})
        for s in mine[1:]:
            channel["geom"].append({"cylinder": [r_w, width], "axis": "z",
                                    "pos": [s["x"], disc_wheel_y(p, s["side"]), 0], "color": ORANGE,
                                    "collide": p["collide"]})
        motors.append({"slot": f"shooter{g}", "joint": f"spin{g}", "motor": p["motor"], "count": p["count"],
                       "gearing": p["gearing"]})
    out = {"part": {"name": "disc_shooter", "format": 1}, "body": bodies, "motor": motors,
           "mount": [{"name": "base", "body": "base"}]}
    if articulated:
        motors.append({"slot": "tilt", "joint": "tilt", "motor": p["tilt_motor"], "count": p["tilt_count"],
                       "gearing": p["tilt_gearing"]})
        out["encoder"] = [{"slot": "tilt_encoder", "joint": "tilt", "ratio": 1.0}]
    return out


register(Stock(
    "disc_shooter", N_("planar disc shooter: wheels driving a flat disc along a channel against a fence (or a second "
    "row of wheels), the channel tilted to the launch angle, fixed or on a motor; the test range's [shooter] "
    "kind = \"disc\" runs it with the disc launcher model"),
    (Param("layout", "", "linear", N_("linear: wheels in a row on one side, a straight fence on the other; arc: one "
           "wheel with a fence curved round it; pinch: wheels on both sides, no fence"), kind="str",
           choices=("linear", "arc", "pinch")),
     Param("wheel_diameter", "m", 0.1016, N_("wheel diameter"), positive=True),
     Param("wheel_width", "m", 0.0234, N_("wheel tread width (the contact patch is the narrower of it and the disc)"),
           positive=True),
     Param("wheel_count", "", 4, N_("wheels on each wheeled side (an arc: 1)"), kind="int", min=1),
     Param("wheel_start", "m", 0.10, N_("the first wheel's center, downrange from the disc's center at the entry"),
           min=0.0),
     Param("wheel_pitch", "m", 0.10, N_("the distance between neighboring wheels along the channel"), positive=True),
     Param("wheel_side", "", "right", N_("the wheels' side looking downrange (linear and arc)"), kind="str",
           choices=("right", "left")),
     Param("groups", "", "single", N_("the wheel groups, one shaft and motor slot each: single (every wheel on one "
           "shaft), per_side (a pinch's two sides), per_wheel"), kind="str", choices=GROUPS),
     Param("compression", "m", 0.0127, N_("the disc's diameter less the gap between a wheel and the fence (the other "
           "row in a pinch)"), positive=True),
     Param("channel_length", "m", 0.50, N_("the channel's length: the disc leaves when its trailing rim passes it"),
           positive=True),
     Param("arc_deg", "deg", 90.0, N_("how far the arc's fence wraps its wheel (arc only, at most 180)"), min=1.0,
           max=180.0),
     Param("channel_width", "m", 0.2794, N_("the width between the guide walls: the disc's diameter (the range's "
           "disc must match it)"), positive=True),
     Param("wheel_moi", "kg*m^2", 0.0002, N_("each wheel's (with its hub and share of the shaft) moment of inertia "
           "about its axle"), positive=True),
     Param("added_moi", "kg*m^2", 0.0, N_("a flywheel on each group's shaft"), min=0.0),
     _motor_param("wheel motor model"),
     _count_param("motors driving each wheel group", 2),
     Param("gearing", "", 0.75, N_("motor turns per wheel turn"), positive=True),
     Param("channel_mass", "kg", 2.0, N_("the channel's mass (its plates, fence and wheels' frame)"), positive=True),
     Param("tilt", "", "fixed", N_("fixed: the channel stays at tilt_angle; articulated: it pivots at its entry on a "
           "motor (joint tilt)"), kind="str", choices=("fixed", "articulated")),
     Param("tilt_angle", "deg", 25.0, N_("the fixed channel's launch angle above horizontal"), min=-80.0, max=80.0),
     Param("tilt_range", "deg", [10.0, 45.0], N_("the articulated channel's launch angles at its low and high hard "
           "stops"), kind="pair"),
     Param("tilt_motor", "", "KrakenX60", N_("tilt motor model"), kind="str", choices=_MOTOR_NAMES),
     Param("tilt_count", "", 1, N_("motors driving the tilt"), kind="int", min=1),
     Param("tilt_gearing", "", 100.0, N_("tilt motor turns per channel turn"), positive=True),
     COLLIDE),
    _disc_shooter, slots=("shooter0",),
    mounts=("base",),
    note=N_("Slots: shooter<g> (motor on joint spin<g>, one per wheel group: shooter0, shooter1, ...), and tilt "
         "(motor) and tilt_encoder (channel turns) with an articulated tilt. The channel body's origin is the disc's center at the entry: x downrange, "
         "y left, z normal to the channel (the disc's axis); it is the base's frame pitched up by the launch angle "
         "about y. The tilt joint reads 0 at the low stop: launch angle = tilt_range[0] + the joint's angle. In the "
         "world the [shooter] section's disc launcher model runs the wheel shafts and the spin joints mirror them.")))
